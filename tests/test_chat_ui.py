"""网页对话按时间排列，重试与终局沿用同一个输入流程。"""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from game import Game
import memory
from memory import Memory
import streamlit as st
import world_state
from world_state import WorldState

try:
    from streamlit.testing.v1 import AppTest
except ImportError:
    AppTest = None


class ChatGame(Game):
    def __init__(self):
        super().__init__(WorldState(), Memory(), object())
        self.calls = []
        self.block_calls = []
        self.fail_next = False
        self.state.data['options'] = ['查看石门']

    def run_turn(self, text):
        self.calls.append(text)
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError('临时失败')
        self.state.data['meta']['turn'] += 1
        return '行动回应：' + text

    def run_block(self, plan, days, seclusion=False):
        self.block_calls.append((plan, days, seclusion))
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError('临时失败')
        self.state.data['meta']['turn'] += 1
        # 与真实 _resolve_block 一致：结算完成即清掉被打断的安排
        self.state.data.pop('pending_block', None)
        return '岁月回应：' + plan

    def resume_block(self):
        pending = self.pending_block or {}
        return self.run_block(pending.get('plan') or '日常度日',
                              pending.get('remaining') or 10,
                              pending.get('seclusion') is True)


@unittest.skipIf(AppTest is None, '页面检查需要安装 Streamlit')
class ChatUITests(unittest.TestCase):
    def make_app(self):
        game = ChatGame()
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'))
        app.session_state['game'] = game
        app.session_state['llm'] = game.llm
        app.session_state['log'] = [
            {'role': 'narrator', 'content': '开场故事'},
            {'role': 'player', 'content': '早先行动'},
            {'role': 'narrator', 'content': '早先回应'},
        ]
        app.run()
        self.assertFalse(app.exception)
        return app, game

    def messages(self, app):
        return [m.value for m in app.markdown if m.value.startswith('<div class="message">')]

    @patch('config.list_save_slots', return_value=[])
    def test_send_order_and_input_outside_scrolling_history(self, _slots):
        app, game = self.make_app()
        before = self.messages(app)
        self.assertEqual(len(before), 3)
        for message, expected in zip(before, ['开场故事', '早先行动', '早先回应']):
            self.assertIn(expected, message)
        for action in ['观察石门', '轻敲三下']:
            app.chat_input(key='chat_input').set_value(action).run()
            self.assertFalse(app.exception)
            messages = self.messages(app)
            self.assertIn(action, messages[-2])
            self.assertIn('行动回应：' + action, messages[-1])
        self.assertEqual(game.calls, ['观察石门', '轻敲三下'])
        self.assertEqual(self.messages(app)[:3], before)
        blocks = app.get('flex_container')
        chat = next(b for b in blocks if '-chat_panel_' in b.proto.id)
        composer = next(b for b in blocks if b.proto.id.endswith('-composer'))
        self.assertTrue(chat.proto.autoscroll)
        self.assertFalse(chat.chat_input)
        self.assertEqual(len(composer.chat_input), 1)

    @patch('config.list_save_slots', return_value=[])
    def test_retry_keeps_one_player_message_and_option_can_send(self, _slots):
        app, game = self.make_app()
        game.fail_next = True
        app.chat_input(key='chat_input').set_value('测试重试').run()
        self.assertFalse(app.exception)
        self.assertEqual(len(self.messages(app)), 4)
        self.assertTrue(app.error)
        next(b for b in app.button if b.label == '重试这次行动').click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertEqual(len(self.messages(app)), 5)
        self.assertEqual(game.calls, ['测试重试', '测试重试'])
        next(b for b in app.button if b.label == '查看石门').click().run()
        self.assertEqual(game.calls[-1], '查看石门')
        self.assertIn('行动回应：查看石门', self.messages(app)[-1])

    @patch('config.list_save_slots', return_value=[])
    def test_terminal_disables_input_and_hides_actions(self, _slots):
        app, game = self.make_app()
        game.state.mark_dead('测试结局')
        app.run()
        self.assertFalse(app.exception)
        self.assertTrue(app.chat_input(key='chat_input').disabled)
        self.assertFalse(any(b.label == '查看石门' for b in app.button))
        self.assertTrue(any('这段人生已经结束' in i.value for i in app.info))
        self.assertFalse(game.calls)


    @patch('config.list_save_slots', return_value=[])
    def test_block_plan_starts_a_seasons_block(self, _slots):
        app, game = self.make_app()
        # 空计划 → 提示，不发起结算
        next(b for b in app.button if b.label == '开始').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.warning)
        self.assertFalse(game.block_calls)

        app.text_input(key='block_plan').set_value('闭关冲击筑基').run()
        app.segmented_control(key='block_len').set_value('一年').run()
        app.toggle(key='block_seclusion').set_value(True).run()
        next(b for b in app.button if b.label == '开始').click().run()
        self.assertFalse(app.exception)
        # 界面上应新增一条玩家消息，并按所选时长发起结算
        self.assertEqual(game.block_calls, [('闭关冲击筑基', 360, True)])
        messages = self.messages(app)
        self.assertTrue(any('安排一段时间' in m and '闭关冲击筑基' in m for m in messages))
        self.assertTrue(any('岁月回应：闭关冲击筑基' in m for m in messages))

    @patch('config.list_save_slots', return_value=[])
    def test_interrupted_block_shows_resume_button(self, _slots):
        app, game = self.make_app()
        game.state.data['pending_block'] = {'plan': '在洞府修炼', 'remaining': 350, 'seclusion': True}
        app.run()
        self.assertFalse(app.exception)
        labels = [b.label for b in app.button]
        self.assertTrue(any(l.startswith('继续：在洞府修炼') for l in labels), labels)
        next(b for b in app.button if b.label.startswith('继续：在洞府修炼')).click().run()
        self.assertFalse(app.exception)
        # 继续时按剩余天数结算，结算后按钮消失
        self.assertEqual(game.block_calls, [('在洞府修炼', 350, True)])
        self.assertFalse(any(b.label.startswith('继续：') for b in app.button))


class _FakeQueryParams(dict):
    """模拟 st.query_params（Streamlit 1.62 的 AppTest 不直接暴露地址栏参数）。"""


@unittest.skipIf(AppTest is None, '页面检查需要安装 Streamlit')
class SlotRestoreUITests(unittest.TestCase):
    """P1-12：刷新页面（新会话）后，按地址栏里的槽名恢复上一局进度。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wanjie-ui-slot-')
        self.addCleanup(self.tmp.cleanup)
        for module in (memory, world_state):
            patcher = patch.object(module, 'SAVE_DIR', self.tmp.name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _write_slot(self, key, turn):
        state = WorldState(save_key=key)
        state.apply_template({"world": {"background": "修仙"},
                              "player": {"name": "林舟", "attributes": {"hp": 100, "max_hp": 100}}})
        state.data["meta"]["turn"] = turn
        state.save()
        Memory(save_key=key).save()

    def _app(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'))
        app.session_state['llm'] = object()
        return app

    @patch('config.list_save_slots', return_value=[])
    def test_refresh_restores_game_from_query_slot(self, _slots):
        self._write_slot('临时-424242', turn=3)
        app = self._app()
        with patch.object(st, 'query_params', _FakeQueryParams(slot='临时-424242')):
            app.run()
        self.assertFalse(app.exception)
        game = app.session_state['game'] if 'game' in app.session_state else None
        self.assertIsNotNone(game)
        self.assertEqual(game.state.data['meta']['turn'], 3)
        self.assertEqual(app.session_state['session_slot'], '临时-424242')
        self.assertTrue(any('已恢复上次的进度' in m.value for m in app.markdown))

    @patch('config.list_save_slots', return_value=[])
    def test_stale_slot_does_not_block_fresh_start(self, _slots):
        app = self._app()
        with patch.object(st, 'query_params', _FakeQueryParams(slot='临时-999999')):
            app.run()
        self.assertFalse(app.exception)
        self.assertNotIn('game', app.session_state)


if __name__ == '__main__':
    unittest.main()
