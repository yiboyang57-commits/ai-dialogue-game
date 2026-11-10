"""网页对话按时间排列，重试与终局沿用同一个输入流程。"""
import tempfile
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from game import Game
import memory
from memory import Memory
import world_state
from world_state import WorldState

try:
    import streamlit as st
except ImportError:  # 没装 streamlit 时整个 UI 测试文件跳过，引擎测试仍可单独跑
    st = None

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
        app.session_state['_temp_slots_pruned'] = True  # 测试不清理真实 save/
        app.session_state['log'] = [
            {'role': 'narrator', 'content': '开场故事'},
            {'role': 'player', 'content': '早先行动'},
            {'role': 'narrator', 'content': '早先回应'},
        ]
        app.run()
        self.assertFalse(app.exception)
        return app, game

    @patch('config.list_save_slots', return_value=[])
    def test_storage_failure_notice_only_when_enabled(self, _slots):
        app, game = self.make_app()
        game.storage_errors = 2
        with patch.dict(os.environ, {"WANJIE_STORAGE": "sqlite"}):
            app.run()
        self.assertTrue(any("历史库有 2 回合未写入" in c.value for c in app.caption))
        with patch.dict(os.environ, {"WANJIE_STORAGE": ""}):
            app.run()
        self.assertFalse(any("历史库有 2 回合未写入" in c.value for c in app.caption))

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

    def _write_slot(self, key, turn, recent=None):
        state = WorldState(save_key=key)
        state.apply_template({"world": {"background": "修仙"},
                              "player": {"name": "林舟", "attributes": {"hp": 100, "max_hp": 100}}})
        state.data["meta"]["turn"] = turn
        state.save()
        mem = Memory(save_key=key)
        if recent:
            mem.recent = [{"role": role, "content": text} for role, text in recent]
        mem.save()

    def _messages(self, app):
        return [m.value for m in app.markdown if m.value.startswith('<div class="message">')]

    def _app(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'))
        app.session_state['llm'] = object()
        app.session_state['_temp_slots_pruned'] = True  # 测试不清理真实 save/
        return app

    @patch('config.list_save_slots', return_value=[])
    def test_query_cannot_restore_named_slot(self, _slots):
        self._write_slot('111', turn=23)
        app = self._app()
        fake = _FakeQueryParams(slot='111')
        with patch.object(st, 'query_params', fake):
            app.run()
        self.assertFalse(app.exception)
        self.assertNotIn('game', app.session_state)
        self.assertNotIn('slot', fake)
        self.assertEqual(WorldState.load('111').data['meta']['turn'], 23)

    def test_new_temp_slot_uses_random_token_and_is_reused(self):
        # 只装载实际地址栏流程函数，避免导入 app 时执行整页或读真实存档。
        import ast
        import re
        import secrets
        from types import SimpleNamespace
        source = Path(__file__).resolve().parents[1].joinpath('app.py').read_text()
        tree = ast.parse(source)
        selected = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name in {'_slot_from_query', '_set_slot_query', 'session_slot'}]
        class Session(dict):
            def __getattr__(self, name):
                return self[name]
            def __setattr__(self, name, value):
                self[name] = value
        fake = SimpleNamespace(session_state=Session(), query_params={})
        scope = {'st': fake, 're': re, 'secrets': secrets, 'TEMP_SLOT_BYTES': 12}
        exec(compile(ast.Module(body=selected, type_ignores=[]), 'app.py', 'exec'), scope)
        with patch.object(secrets, 'token_urlsafe', wraps=secrets.token_urlsafe) as token:
            slot = scope['session_slot']()
            self.assertEqual(scope['session_slot'](), slot)
            token.assert_called_once_with(12)
        self.assertRegex(slot, r'^临时-[A-Za-z0-9_-]{16}$')
        self.assertEqual(fake.query_params['slot'], slot)
        scope['_set_slot_query']('111')
        self.assertNotIn('slot', fake.query_params)

    @patch('config.list_save_slots', return_value=[])
    def test_refresh_restores_game_from_query_slot(self, _slots):
        self._write_slot('临时-424242', turn=3)
        app = self._app()
        fake = _FakeQueryParams(slot='临时-424242')
        with patch.object(st, 'query_params', fake):
            app.run()
        self.assertFalse(app.exception)
        game = app.session_state['game'] if 'game' in app.session_state else None
        self.assertIsNotNone(game)
        self.assertEqual(game.state.data['meta']['turn'], 3)
        self.assertEqual(app.session_state['session_slot'], '临时-424242')
        self.assertTrue(any('已恢复上次的进度' in m.value for m in app.markdown))
        self.assertEqual(fake['slot'], '临时-424242')  # 审查 L2：地址栏槽名被保留

    @patch('config.list_save_slots', return_value=[])
    def test_stale_slot_does_not_block_fresh_start(self, _slots):
        app = self._app()
        fake = _FakeQueryParams(slot='临时-999999')
        with patch.object(st, 'query_params', fake):
            app.run()
        self.assertFalse(app.exception)
        self.assertNotIn('game', app.session_state)
        self.assertNotIn('slot', fake)  # 失效槽名被清掉，新局不会写进它

    @patch('config.list_save_slots', return_value=[])
    def test_corrupt_slot_is_not_overwritten(self, _slots):
        """审查 M2：损坏存档不能被新局静默覆盖，地址栏槽名要清掉。"""
        corrupt = Path(self.tmp.name) / 'state_临时-999999.json'
        corrupt.write_text('{"meta": 半截', encoding='utf-8')
        before = corrupt.read_bytes()
        app = self._app()
        fake = _FakeQueryParams(slot='临时-999999')
        with patch.object(st, 'query_params', fake):
            app.run()
        self.assertFalse(app.exception)
        self.assertNotIn('game', app.session_state)
        self.assertNotIn('slot', fake)
        self.assertNotIn('session_slot', app.session_state)
        self.assertTrue(any('已损坏' in w.value for w in app.warning))
        self.assertEqual(corrupt.read_bytes(), before)  # 原文件未被覆盖

    @patch('config.list_save_slots', return_value=[])
    def test_stop_world_clears_query_slot(self, _slots):
        """审查 L2：「停止世界 → 不保存」后地址栏槽名必须清掉。"""
        self._write_slot('临时-424242', turn=3)
        app = self._app()
        fake = _FakeQueryParams(slot='临时-424242')
        with patch.object(st, 'query_params', fake):
            app.run()
            next(b for b in app.button if b.label == '停止世界').click().run()
            next(b for b in app.button if b.label == '不保存').click().run()
        self.assertFalse(app.exception)
        self.assertNotIn('slot', fake)
        self.assertNotIn('game', app.session_state)
        self.assertNotIn('session_slot', app.session_state)

    @patch('config.list_save_slots', return_value=[])
    def test_restore_shows_recent_dialogue(self, _slots):
        """P2-18：刷新恢复后，对话区要接上最近几轮原文，而不是只剩状态摘要。"""
        self._write_slot('临时-424242', turn=3,
                         recent=[("user", "早先玩家说的话"), ("assistant", "早先主持人的话")])
        app = self._app()
        fake = _FakeQueryParams(slot='临时-424242')
        with patch.object(st, 'query_params', fake):
            app.run()
        self.assertFalse(app.exception)
        joined = "\n".join(self._messages(app))
        self.assertIn("早先玩家说的话", joined)
        self.assertIn("早先主持人的话", joined)

    @patch('config.list_save_slots', return_value=[{"key": "命名档", "name": "命名档", "updated": ""}])
    def test_loading_named_slot_shows_recent_dialogue(self, _slots):
        """P2-18：从「继续存档」读命名档时同样接上最近几轮。"""
        self._write_slot('命名档', turn=2, recent=[("user", "上次我去了集市")])
        app = self._app()
        fake = _FakeQueryParams()
        with patch.object(st, 'query_params', fake):
            app.run()
            next(b for b in app.button if b.label == '命名档').click().run()
        self.assertNotIn('slot', fake)
        self.assertFalse(app.exception)
        self.assertIn("上次我去了集市", "\n".join(self._messages(app)))

    @patch('config.prune_temp_slots', return_value=[])
    @patch('config.list_save_slots', return_value=[])
    def test_temp_slots_pruned_once_per_session(self, _slots, prune):
        """P2-17：启动时清理一次临时槽，rerun 不重复清理。"""
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'))
        app.session_state['llm'] = object()
        app.run()
        self.assertEqual(prune.call_count, 1)
        app.run()
        self.assertEqual(prune.call_count, 1)
        self.assertFalse(app.exception)


if __name__ == '__main__':
    unittest.main()
