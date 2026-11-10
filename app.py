"""AI 对话模拟器 · Streamlit 精致版界面。

运行：streamlit run app.py
复用命令行/图形版的核心逻辑（game.py / world_state.py / judgment.py …），
这里只做界面呈现层：顺序对话、独立输入区与卡片化世界状态。
"""
import html
import random
import re

import streamlit as st

from config import BASE_URL, SaveCorruptError, load_api_key, save_api_key, load_model, list_save_slots
from game import Game
from llm_client import LLMClient, LLMError
from memory import Memory
from world_state import WorldState
import character_builder
import dynamics
import save_bundle
import seasons
import world_gen

# ---------------------------------------------------------------- 主题与全局样式
# 古卷水墨 · 册页版：宣纸、浓墨、朱砂印，正文区是一页线装书（样式全部在下面这段 CSS 里）
VERSION = "v2.21"  # 界面版本号：用于确认云端是否已部署最新代码（顶栏可见）
CHAT_HEIGHT = 500  # 提供滚动容器，实际高度由响应式样式调整

st.set_page_config(page_title="万界 · 文字冒险", page_icon="🗡️", layout="wide",
                   initial_sidebar_state="collapsed")

st.markdown("""
<style>
/* ============ 古卷水墨 · 册页版 ============
   宣纸为底、浓墨为字、朱砂只用于印章与关键操作；页脚一抹远山。
   正文区做成线装书的一页：外粗内细的版框，页眉写地点与时辰，回目印盖在页眉右侧。
   「万界」两字用马善政毛笔字（已裁成只含这两个字的小字体，内嵌，离线可用）。 */
@font-face { font-family:"WJ Brush"; font-display:block; unicode-range:U+754C;
    src:url(data:font/woff2;base64,d09GMgABAAAAAARMAA4AAAAABuAAAAP6AAIAxQAAAAAAAAAAAAAAAAAAAAAAAAAAGyAcIAZgADQRCAqFBIN/ATYCJAMGCwYABCAFg14HIAwHG18FUZQsSh/gLwXcGEKf6C1RyaHnj1bo6s1W20URdifbtpwjbHnllQ9AsNroPhvNMMYotHUXW4qiOiI8AmqO3EvyRKhLxL7sGI1Bdh3/wnWqANjq99ve12aG2nQSRJImvFrJr+IJzxqSZfpPHL+fm7kjLsk89AeNUe9j5qFNIiGxRt/EOJuQ1wS3JbCBzLs+N+Lf6dNfR4s/cM5FhI5nkaAzTE6bPimKYPayaL/e/vSClkBBW0BxCxMOPLTUgtPBIceSwg1aq3k9wPGHb+Jy9Gsu8dmwCZQmnnWBF+UJ5YETDjHkNKIE4oPgN/+X40/3jk5o4iGfyNEYy4/XsjZGGGi6KGoDZ1niBajB5+6jeL/XrHGUnyeATBEa+HETaltxCALwH6BmdxJ/wikpEB2JFgGYGm8WVhAHJ0i6piWLy0pwX1unjTOJ/C/xi7AVQADCr2jUIwAgFoqRCDJJAk5SJNg6LU2jHLkNARAon+TBCcdJQAp4gg/kgWcPhmJbTkyajYfiw1Xc4E+uLffwIALpvPe842mGZ/NTw8Nol7k4lYEITpA+bHFhTyTKXU0AYgwz79pfq9adGUsMT9yLASR7lStqnkzzFKWJR/3KQvdRFM03MyzNS+e32z3mc8f1Me8+eDhgzB9ML4z5/LE09vhi2bz3+Pt74ZYI9vC7ra2kmnnpcc32Fnx3Hs9mW/Hlf7vVi2Jx/pYh7W22UbQox6FHcgQmUeSCWa3uXjp/rVzGs7nWeSU57O9mzEG+uDhv3Z9vkXSMU/spg+/Wnvr96Jc8TAFl5Y6qyplZ1Rdd/IYadREz1nr3fu4H3Z15kvEl7Wcc6d9gnDTylLZ5a6unkmH3zQWZHlPraX9cPPsunFEYd4fDJMF3OwNFYomr6RmFlY5jX0Houk7+rtvEHFxNlf9xypSc58jw/vP/Q+NuWZb8YxJrDM8x9695af89uhmPDflLPJ/H/jxNYqafnsUYHOjR0Jze4vbu15mmif8sbWZVvmORr+E35r7PqC7jpvO5Iu73ep/urv3cvuwsotg23vOoHRBA5NeW4/BDnr6mA39haMutAzw78Cxof5S38Vmk4nayn5KpgOmueUGEkI33GebxSWTgGQdFpBsjkGa4GiUkAHcAJSF3KEcRoLyKX1RQCoyKQqGeI8mFZpFX0FSSFJdQQRTg45dJpzULotvrBDklBOUEeKWcOKJGJXkpUWEVREWqKhLySsqIbEZUVBSUqXl5DUuqSKgK4euXl0XI66lxGVExeTkVZQKTgsreUEti3UkjskhEcMTfJq4qI6hEzMPHJyhHNz8bC82LVvD2T+qzikjkEX1zlJSv5pxn4WaBCd3/GZS9F/7vv4bHAQA=) format("woff2"); }
@font-face { font-family:"WJ Brush"; font-display:block; unicode-range:U+4E07;
    src:url(data:font/woff2;base64,d09GMgABAAAAAAMUAA4AAAAABdAAAALDAAIAxQAAAAAAAAAAAAAAAAAAAAAAAAAAGyAcIAZgADQRCAqCdII0ATYCJAMGCwYABCAFg14HIAwHG5QEUVQtshA/DnKyUxPOsFtZJVakwPNuyjhSWSj8pGsRfYz99t43ST/hyTQTTbqnQIjQComhNCiJIWQ/mttE2zJ6EZc0a4j4/7AfojmQSQUG5bSD4At2JuusrwG6lqmbwE8vtMKQE8nWxBdrn7cxwoQ30MS/ELPAOPV2UYNi1C3rej1B3wwPev+w1KIkTfiHCARIxpuNX6q0qMdyRznbDsu9RGIIu/Kur0yd+lbEfiuR/V9E4zPxKIF81R8WMCg0MMFKE3IWUJrxcNmBcIimvgS0pPfQgQJNNPCA8kRshcbmg6RgGBhQKWWpF1niRrLXZTHP/y09ihSua5Acb1jmbGYrFalHsQ1ghFoalEvm2YArqNcYEq+TRtoPsm+Kaw/2DgoLs01IgQmuFreRmWxMdgcb6CDIBtkDQWCPZQmRVcdcV2LaX6Lcq7yLFK5rZGYWRc+4YTBuBwOlvyWFSvI+qUIcfWE04Y/p1H7PGeTRDQZKe9k45qrM09kgB0qJWyvqbWWtTh++EwYBHw5xMkw0VgaUrJIoxbCEXyUN6surotRUx/tTRsw3zwVN6C6IBcwp3hL6GSklnrZPohJ03xVJ0XmDY86l3RoiQ6O5U7koTPDqyrqRsLwfT6JwDXcvnXKSMgIEQtlLGhRq8aPT/iZnie4QeIucPUHgo2CWCyr/pUTPRLU6aCsQPlIWANjpuUMtyWfy3KBRBRQ7vACAhqDjGitNmeKFI4opH1TG3lHDnj9qWo0DtUzF4Ne2mAkdYXs5syDYFUiIicsBLdgaKJtrwuaOBovnEQJqzoittaUroLq5woizC+CDXV3ZLgqiohBLdOvNQsQScRCFEASyt7ZBHF1dRB3MXVhDb8P/JAfQBHyRhjXkZm/uLCEiJiapaKDPpCsW1BG+XtSmC8A61iUzCxErxlWYBSxK/bfaFX4NzH/mygE=) format("woff2"); }
:root {
    --paper:#E8E1D0;        /* 宣纸：页面底色 */
    --silk:#F8F4EA;         /* 素绢：阅读区 */
    --ink:#211F1B;          /* 浓墨：正文 */
    --ink-2:#5A554B;        /* 淡墨：次要文字 */
    --ink-3:#645D51;        /* 枯墨：注脚 */
    --frame:rgba(33,31,27,.58);   /* 版框粗线 */
    --line:rgba(33,31,27,.16);
    --line-soft:rgba(33,31,27,.08);
    --cinnabar:#A23A2C;     /* 朱砂 */
    --cinnabar-deep:#862E23;
    --azurite:#3A6283;      /* 石青：提示 */
    --ochre:#8A6417;        /* 赭黄：警示 */
    --serif:"Songti SC","STSong","Noto Serif SC","Noto Serif CJK SC","Source Han Serif SC","SimSun",serif;
    --kai:"Kaiti SC","STKaiti","KaiTi","AR PL UKai CN",var(--serif);
    --grain:url(data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyMjAiIGhlaWdodD0iMjIwIj48ZmlsdGVyIGlkPSJuIiB4PSIwIiB5PSIwIiB3aWR0aD0iMTAwJSIgaGVpZ2h0PSIxMDAlIj48ZmVUdXJidWxlbmNlIHR5cGU9ImZyYWN0YWxOb2lzZSIgYmFzZUZyZXF1ZW5jeT0iLjkgLjM1IiBudW1PY3RhdmVzPSIyIiBzZWVkPSI3IiBzdGl0Y2hUaWxlcz0ic3RpdGNoIi8+PGZlQ29sb3JNYXRyaXggdmFsdWVzPSIwIDAgMCAwIC4zMiAgMCAwIDAgMCAuMjYgIDAgMCAwIDAgLjE2ICAwIDAgMCAuNCAtLjE2NSIvPjwvZmlsdGVyPjxyZWN0IHdpZHRoPSIyMjAiIGhlaWdodD0iMjIwIiBmaWx0ZXI9InVybCgjbikiLz48L3N2Zz4=);
}

/* —— 纸面：纤维纹理 + 页脚远山（游戏中只露出山尖，开局页露出全景与红日） —— */
.stApp {
    color:var(--ink); background-color:var(--paper);
    background-image:url(data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxNjAwIDQyMCIgcHJlc2VydmVBc3BlY3RSYXRpbz0ieE1pZFlNYXggc2xpY2UiPjxkZWZzPjxsaW5lYXJHcmFkaWVudCBpZD0iZzAiIHgxPSIwIiB5MT0iNzIiIHgyPSIwIiB5Mj0iNDIwIiBncmFkaWVudFVuaXRzPSJ1c2VyU3BhY2VPblVzZSI+PHN0b3Agb2Zmc2V0PSIwIiBzdG9wLWNvbG9yPSIjMmIzMDM3IiBzdG9wLW9wYWNpdHk9IjAuMSIvPjxzdG9wIG9mZnNldD0iLjUiIHN0b3AtY29sb3I9IiMyYjMwMzciIHN0b3Atb3BhY2l0eT0iMC4wMzAiLz48c3RvcCBvZmZzZXQ9IjEiIHN0b3AtY29sb3I9IiMyYjMwMzciIHN0b3Atb3BhY2l0eT0iMCIvPjwvbGluZWFyR3JhZGllbnQ+PGxpbmVhckdyYWRpZW50IGlkPSJnMSIgeDE9IjAiIHkxPSIxNzYiIHgyPSIwIiB5Mj0iNDIwIiBncmFkaWVudFVuaXRzPSJ1c2VyU3BhY2VPblVzZSI+PHN0b3Agb2Zmc2V0PSIwIiBzdG9wLWNvbG9yPSIjMmIzMDM3IiBzdG9wLW9wYWNpdHk9IjAuMTUiLz48c3RvcCBvZmZzZXQ9Ii41IiBzdG9wLWNvbG9yPSIjMmIzMDM3IiBzdG9wLW9wYWNpdHk9IjAuMDQ1Ii8+PHN0b3Agb2Zmc2V0PSIxIiBzdG9wLWNvbG9yPSIjMmIzMDM3IiBzdG9wLW9wYWNpdHk9IjAiLz48L2xpbmVhckdyYWRpZW50PjxsaW5lYXJHcmFkaWVudCBpZD0iZzIiIHgxPSIwIiB5MT0iMjkyIiB4Mj0iMCIgeTI9IjQyMCIgZ3JhZGllbnRVbml0cz0idXNlclNwYWNlT25Vc2UiPjxzdG9wIG9mZnNldD0iMCIgc3RvcC1jb2xvcj0iIzJiMzAzNyIgc3RvcC1vcGFjaXR5PSIwLjI0Ii8+PHN0b3Agb2Zmc2V0PSIuNSIgc3RvcC1jb2xvcj0iIzJiMzAzNyIgc3RvcC1vcGFjaXR5PSIwLjA3MiIvPjxzdG9wIG9mZnNldD0iMSIgc3RvcC1jb2xvcj0iIzJiMzAzNyIgc3RvcC1vcGFjaXR5PSIwIi8+PC9saW5lYXJHcmFkaWVudD48L2RlZnM+PHBhdGggZD0iTTAgNDIwTDAgMjExTDE2IDIwMkwzMiAxOTVMNDggMTg1TDY0IDE3NEw4MCAxNThMOTYgMTQ3TDExMiAxMzFMMTI4IDExMEwxNDQgMTAyTDE2MCAxMTRMMTc2IDEzNUwxOTIgMTU0TDIwOCAxNjVMMjI0IDE3NUwyNDAgMTg4TDI1NiAxOTNMMjcyIDE5N0wyODggMjAwTDMwNCAyMDRMMzIwIDIwOUwzMzYgMjE1TDM1MiAyMDlMMzg0IDIwMkw0MDAgMTk3TDQxNiAxOTBMNDMyIDE4OEw0NDggMTk3TDQ2NCAyMDFMNDgwIDIwNEw0OTYgMjExTDUxMiAyMjJMNTI4IDIyN0w1NjAgMjM0TDU3NiAyMzlMNTkyIDIzNkw2MDggMjM0TDYyNCAyMzJMNjU2IDIyM0w2NzIgMjE5TDcwNCAyMTNMNzIwIDIwOUw3MzYgMjA1TDc1MiAxOTdMNzY4IDE5NEw3ODQgMjAwTDgwMCAyMDVMODE2IDIwOEw4MzIgMjE3TDg0OCAyMTlMODY0IDIyM0w4ODAgMjMwTDg5NiAyMzRMOTEyIDIzNkw5MjggMjM0TDk0NCAyMjdMOTYwIDIyNUw5NzYgMjIzTDk5MiAyMTZMMTAwOCAyMDlMMTAyNCAxOTdMMTA0MCAxODVMMTA1NiAxNzRMMTA3MiAxNThMMTA4OCAxNDdMMTEwNCAxNTVMMTEyMCAxNTdMMTEzNiAxNjRMMTE1MiAxNjdMMTE2OCAxNjNMMTE4NCAxNThMMTIxNiAxNTJMMTIzMiAxNDZMMTI0OCAxNDFMMTI2NCAxMzRMMTI4MCAxMjZMMTI5NiAxMTNMMTMxMiA5NEwxMzI4IDcyTDEzNDQgODNMMTM2MCA5OEwxMzc2IDExNUwxMzkyIDEyOUwxNDA4IDEzNkwxNDI0IDE0M0wxNDQwIDE1NEwxNDU2IDE1NkwxNDcyIDE2MEwxNTIwIDE1NUwxNTM2IDE1MEwxNTUyIDE0NEwxNTg0IDE2MkwxNjAwIDE3N0wxNjAwIDQyMFoiIGZpbGw9InVybCgjZzApIi8+PHBhdGggZD0iTTAgNDIwTDAgMTkzTDE2IDIwMEwzMiAyMTNMNDggMjI0TDY0IDIzMUw4MCAyNDBMOTYgMjQ0TDExMiAyNDdMMTI4IDI1MEwxNDQgMjU2TDE3NiAyNjJMMTkyIDI1OUwyMDggMjU2TDIyNCAyNTNMMjU2IDI1MEwyNzIgMjQ1TDI4OCAyMzhMMzA0IDIzMkwzMjAgMjI0TDMzNiAyMjBMMzUyIDIyOEwzNjggMjM1TDM4NCAyNDBMNDAwIDI1MUw0MTYgMjU4TDQzMiAyNjRMNDQ4IDI3MUw0NjQgMjczTDQ5NiAyNzVMNTI4IDI3OUw1NDQgMjc0TDU5MiAyNjhMNjA4IDI2Mkw2NDAgMjU1TDY1NiAyNjBMNjcyIDI2Mkw3MDQgMjY1TDczNiAyNjdMNzUyIDI3Mkw3NjggMjc2TDgzMiAyNzZMODY0IDI3M0w4OTYgMjcxTDkxMiAyNjdMOTI4IDI1OEw5NDQgMjUxTDk2MCAyNDRMOTc2IDI0MEw5OTIgMjQ4TDEwMDggMjU1TDEwMjQgMjYyTDEwNTYgMjY3TDEwNzIgMjcyTDEwODggMjc3TDExMDQgMjgyTDExMjAgMjgwTDExNTIgMjg2TDExNjggMjg4TDExODQgMjkwTDEyMDAgMjg3TDEyMzIgMjgxTDEyNjQgMjc5TDEyODAgMjcxTDEyOTYgMjYxTDEzMTIgMjU2TDEzMjggMjUxTDEzNDQgMjQzTDEzNjAgMjMyTDEzNzYgMjI0TDEzOTIgMjIwTDE0MDggMjA5TDE0MjQgMjA2TDE0NDAgMTk1TDE0NTYgMTg0TDE0NzIgMTc2TDE0ODggMTkwTDE1MDQgMjAyTDE1MjAgMjA4TDE1MzYgMjEyTDE1NTIgMjIyTDE1NjggMjMzTDE1ODQgMjM2TDE2MDAgMjQ0TDE2MDAgNDIwWiIgZmlsbD0idXJsKCNnMSkiLz48cGF0aCBkPSJNMCA0MjBMMCAzMzRMMzIgMzI5TDY0IDMyN0w4MCAzMjRMOTYgMzIyTDExMiAzMTlMMTI4IDMxNEwxNDQgMzEyTDE2MCAzMTBMMTc2IDMwN0wyNDAgMzA5TDI3MiAzMTZMMjg4IDMyMEwzMDQgMzIzTDMzNiAzMjZMMzY4IDMyOUw0MDAgMzMxTDQxNiAzMzRMNDMyIDMzNkw0NjQgMzM0TDQ5NiAzMzdMNTI4IDMzOUw1NjAgMzM3TDU3NiAzMzlMNTkyIDMzNUw2MDggMzMyTDY1NiAzMzBMNjg4IDMyN0w3MjAgMzMxTDczNiAzMzNMNzY4IDMzNUw3ODQgMzM4TDgzMiAzNDFMODk2IDM0MUw5NjAgMzQxTDk3NiAzMzlMOTkyIDMzNkwxMDQwIDMzMUwxMDU2IDMyNkwxMTA0IDMyM0wxMTIwIDMxOEwxMTUyIDMxNkwxMTY4IDMxMUwxMTg0IDMwOUwxMjAwIDMxM0wxMjMyIDMxNkwxMjY0IDMyMUwxMjk2IDMyM0wxMzEyIDMyNkwxMzQ0IDMyOEwxNDA4IDMzMUwxNDI0IDMyOUwxNDQwIDMyNkwxNDU2IDMyM0wxNDcyIDMyMEwxNTA0IDMxNUwxNTIwIDMxMkwxNTM2IDMxMEwxNTUyIDMwN0wxNTY4IDMwMUwxNTg0IDI5OEwxNjAwIDI5MkwxNjAwIDQyMFoiIGZpbGw9InVybCgjZzIpIi8+PC9zdmc+), var(--grain);
    background-repeat:no-repeat, repeat;
    background-size:max(100%, 1100px) auto, 220px 220px;
    background-position:center bottom -150px, 0 0;
}
.stApp:has(.wj-cover) {
    background-image:url(data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxNjAwIDQyMCIgcHJlc2VydmVBc3BlY3RSYXRpbz0ieE1pZFlNYXggc2xpY2UiPjxkZWZzPjxsaW5lYXJHcmFkaWVudCBpZD0iZzAiIHgxPSIwIiB5MT0iNzIiIHgyPSIwIiB5Mj0iNDIwIiBncmFkaWVudFVuaXRzPSJ1c2VyU3BhY2VPblVzZSI+PHN0b3Agb2Zmc2V0PSIwIiBzdG9wLWNvbG9yPSIjMmIzMDM3IiBzdG9wLW9wYWNpdHk9IjAuMSIvPjxzdG9wIG9mZnNldD0iLjUiIHN0b3AtY29sb3I9IiMyYjMwMzciIHN0b3Atb3BhY2l0eT0iMC4wMzAiLz48c3RvcCBvZmZzZXQ9IjEiIHN0b3AtY29sb3I9IiMyYjMwMzciIHN0b3Atb3BhY2l0eT0iMCIvPjwvbGluZWFyR3JhZGllbnQ+PGxpbmVhckdyYWRpZW50IGlkPSJnMSIgeDE9IjAiIHkxPSIxNzYiIHgyPSIwIiB5Mj0iNDIwIiBncmFkaWVudFVuaXRzPSJ1c2VyU3BhY2VPblVzZSI+PHN0b3Agb2Zmc2V0PSIwIiBzdG9wLWNvbG9yPSIjMmIzMDM3IiBzdG9wLW9wYWNpdHk9IjAuMTUiLz48c3RvcCBvZmZzZXQ9Ii41IiBzdG9wLWNvbG9yPSIjMmIzMDM3IiBzdG9wLW9wYWNpdHk9IjAuMDQ1Ii8+PHN0b3Agb2Zmc2V0PSIxIiBzdG9wLWNvbG9yPSIjMmIzMDM3IiBzdG9wLW9wYWNpdHk9IjAiLz48L2xpbmVhckdyYWRpZW50PjxsaW5lYXJHcmFkaWVudCBpZD0iZzIiIHgxPSIwIiB5MT0iMjkyIiB4Mj0iMCIgeTI9IjQyMCIgZ3JhZGllbnRVbml0cz0idXNlclNwYWNlT25Vc2UiPjxzdG9wIG9mZnNldD0iMCIgc3RvcC1jb2xvcj0iIzJiMzAzNyIgc3RvcC1vcGFjaXR5PSIwLjI0Ii8+PHN0b3Agb2Zmc2V0PSIuNSIgc3RvcC1jb2xvcj0iIzJiMzAzNyIgc3RvcC1vcGFjaXR5PSIwLjA3MiIvPjxzdG9wIG9mZnNldD0iMSIgc3RvcC1jb2xvcj0iIzJiMzAzNyIgc3RvcC1vcGFjaXR5PSIwIi8+PC9saW5lYXJHcmFkaWVudD48L2RlZnM+PGNpcmNsZSBjeD0iMTQ0MCIgY3k9IjEyMiIgcj0iMzAiIGZpbGw9IiNhNjM5MmIiIGZpbGwtb3BhY2l0eT0iLjcyIi8+PHBhdGggZD0iTTAgNDIwTDAgMjExTDE2IDIwMkwzMiAxOTVMNDggMTg1TDY0IDE3NEw4MCAxNThMOTYgMTQ3TDExMiAxMzFMMTI4IDExMEwxNDQgMTAyTDE2MCAxMTRMMTc2IDEzNUwxOTIgMTU0TDIwOCAxNjVMMjI0IDE3NUwyNDAgMTg4TDI1NiAxOTNMMjcyIDE5N0wyODggMjAwTDMwNCAyMDRMMzIwIDIwOUwzMzYgMjE1TDM1MiAyMDlMMzg0IDIwMkw0MDAgMTk3TDQxNiAxOTBMNDMyIDE4OEw0NDggMTk3TDQ2NCAyMDFMNDgwIDIwNEw0OTYgMjExTDUxMiAyMjJMNTI4IDIyN0w1NjAgMjM0TDU3NiAyMzlMNTkyIDIzNkw2MDggMjM0TDYyNCAyMzJMNjU2IDIyM0w2NzIgMjE5TDcwNCAyMTNMNzIwIDIwOUw3MzYgMjA1TDc1MiAxOTdMNzY4IDE5NEw3ODQgMjAwTDgwMCAyMDVMODE2IDIwOEw4MzIgMjE3TDg0OCAyMTlMODY0IDIyM0w4ODAgMjMwTDg5NiAyMzRMOTEyIDIzNkw5MjggMjM0TDk0NCAyMjdMOTYwIDIyNUw5NzYgMjIzTDk5MiAyMTZMMTAwOCAyMDlMMTAyNCAxOTdMMTA0MCAxODVMMTA1NiAxNzRMMTA3MiAxNThMMTA4OCAxNDdMMTEwNCAxNTVMMTEyMCAxNTdMMTEzNiAxNjRMMTE1MiAxNjdMMTE2OCAxNjNMMTE4NCAxNThMMTIxNiAxNTJMMTIzMiAxNDZMMTI0OCAxNDFMMTI2NCAxMzRMMTI4MCAxMjZMMTI5NiAxMTNMMTMxMiA5NEwxMzI4IDcyTDEzNDQgODNMMTM2MCA5OEwxMzc2IDExNUwxMzkyIDEyOUwxNDA4IDEzNkwxNDI0IDE0M0wxNDQwIDE1NEwxNDU2IDE1NkwxNDcyIDE2MEwxNTIwIDE1NUwxNTM2IDE1MEwxNTUyIDE0NEwxNTg0IDE2MkwxNjAwIDE3N0wxNjAwIDQyMFoiIGZpbGw9InVybCgjZzApIi8+PHBhdGggZD0iTTAgNDIwTDAgMTkzTDE2IDIwMEwzMiAyMTNMNDggMjI0TDY0IDIzMUw4MCAyNDBMOTYgMjQ0TDExMiAyNDdMMTI4IDI1MEwxNDQgMjU2TDE3NiAyNjJMMTkyIDI1OUwyMDggMjU2TDIyNCAyNTNMMjU2IDI1MEwyNzIgMjQ1TDI4OCAyMzhMMzA0IDIzMkwzMjAgMjI0TDMzNiAyMjBMMzUyIDIyOEwzNjggMjM1TDM4NCAyNDBMNDAwIDI1MUw0MTYgMjU4TDQzMiAyNjRMNDQ4IDI3MUw0NjQgMjczTDQ5NiAyNzVMNTI4IDI3OUw1NDQgMjc0TDU5MiAyNjhMNjA4IDI2Mkw2NDAgMjU1TDY1NiAyNjBMNjcyIDI2Mkw3MDQgMjY1TDczNiAyNjdMNzUyIDI3Mkw3NjggMjc2TDgzMiAyNzZMODY0IDI3M0w4OTYgMjcxTDkxMiAyNjdMOTI4IDI1OEw5NDQgMjUxTDk2MCAyNDRMOTc2IDI0MEw5OTIgMjQ4TDEwMDggMjU1TDEwMjQgMjYyTDEwNTYgMjY3TDEwNzIgMjcyTDEwODggMjc3TDExMDQgMjgyTDExMjAgMjgwTDExNTIgMjg2TDExNjggMjg4TDExODQgMjkwTDEyMDAgMjg3TDEyMzIgMjgxTDEyNjQgMjc5TDEyODAgMjcxTDEyOTYgMjYxTDEzMTIgMjU2TDEzMjggMjUxTDEzNDQgMjQzTDEzNjAgMjMyTDEzNzYgMjI0TDEzOTIgMjIwTDE0MDggMjA5TDE0MjQgMjA2TDE0NDAgMTk1TDE0NTYgMTg0TDE0NzIgMTc2TDE0ODggMTkwTDE1MDQgMjAyTDE1MjAgMjA4TDE1MzYgMjEyTDE1NTIgMjIyTDE1NjggMjMzTDE1ODQgMjM2TDE2MDAgMjQ0TDE2MDAgNDIwWiIgZmlsbD0idXJsKCNnMSkiLz48cGF0aCBkPSJNMCA0MjBMMCAzMzRMMzIgMzI5TDY0IDMyN0w4MCAzMjRMOTYgMzIyTDExMiAzMTlMMTI4IDMxNEwxNDQgMzEyTDE2MCAzMTBMMTc2IDMwN0wyNDAgMzA5TDI3MiAzMTZMMjg4IDMyMEwzMDQgMzIzTDMzNiAzMjZMMzY4IDMyOUw0MDAgMzMxTDQxNiAzMzRMNDMyIDMzNkw0NjQgMzM0TDQ5NiAzMzdMNTI4IDMzOUw1NjAgMzM3TDU3NiAzMzlMNTkyIDMzNUw2MDggMzMyTDY1NiAzMzBMNjg4IDMyN0w3MjAgMzMxTDczNiAzMzNMNzY4IDMzNUw3ODQgMzM4TDgzMiAzNDFMODk2IDM0MUw5NjAgMzQxTDk3NiAzMzlMOTkyIDMzNkwxMDQwIDMzMUwxMDU2IDMyNkwxMTA0IDMyM0wxMTIwIDMxOEwxMTUyIDMxNkwxMTY4IDMxMUwxMTg0IDMwOUwxMjAwIDMxM0wxMjMyIDMxNkwxMjY0IDMyMUwxMjk2IDMyM0wxMzEyIDMyNkwxMzQ0IDMyOEwxNDA4IDMzMUwxNDI0IDMyOUwxNDQwIDMyNkwxNDU2IDMyM0wxNDcyIDMyMEwxNTA0IDMxNUwxNTIwIDMxMkwxNTM2IDMxMEwxNTUyIDMwN0wxNTY4IDMwMUwxNTg0IDI5OEwxNjAwIDI5MkwxNjAwIDQyMFoiIGZpbGw9InVybCgjZzIpIi8+PC9zdmc+), var(--grain);
    background-position:center bottom, 0 0;
}
.block-container {
    padding:calc(3rem + env(safe-area-inset-top, 0px)) 2rem 2rem;
    max-width:1280px;
    container-type:inline-size;
}
header[data-testid="stHeader"] { background-color:var(--paper); background-image:var(--grain); background-size:220px 220px; }
.stApp h1, .stApp h2, .stApp h3 { font-family:var(--serif); color:var(--ink); letter-spacing:.06em; }
.stApp h3 { font-size:22px; }
:focus-visible { outline-color:var(--cinnabar); }

/* —— 题头：毛笔「万界」+ 四字方印 —— */
.gm-header {
    display:flex; align-items:flex-end; justify-content:space-between; gap:16px;
    padding:0 2px 12px; margin-bottom:4px; border-bottom:1px solid var(--line);
}
.gm-brand { display:flex; align-items:center; flex-wrap:wrap; gap:6px 14px; }
.gm-title { font-family:"WJ Brush", var(--kai); font-size:50px; line-height:1; color:var(--ink);
    letter-spacing:.06em; }
.gm-header.is-game .gm-title { font-size:40px; }
.gm-mark {
    flex:none; display:inline-block; writing-mode:vertical-rl; height:2.5em; box-sizing:content-box;
    font-family:var(--serif); font-weight:700; font-size:11px; line-height:1.25; letter-spacing:.14em;
    color:#F8EFE6; background:var(--cinnabar); padding:4px 4px 2px; border-radius:2px;
    box-shadow:inset 0 0 0 1.5px var(--cinnabar), inset 0 0 0 2.5px rgba(248,239,230,.5);
}
.gm-page { font-family:var(--serif); font-size:22px; font-weight:700; color:var(--ink); letter-spacing:.14em;
    padding-left:14px; border-left:1px solid var(--line); }
.gm-ver { font-size:10px; color:var(--ink-3); letter-spacing:.02em; align-self:flex-end; padding-bottom:4px; }
.gm-save { font-family:var(--kai); font-size:13px; color:var(--ink-3); padding-bottom:3px; white-space:nowrap;
    overflow:hidden; text-overflow:ellipsis; max-width:50%; }

/* —— 开局页：线装书封面上的题签 —— */
.wj-cover { display:flex; flex-direction:column; align-items:center; gap:10px; padding-top:6px; }
.wj-slip {
    position:relative; writing-mode:vertical-rl; display:flex; align-items:center; gap:22px;
    background:var(--silk); border:1.5px solid var(--frame); padding:30px 20px 24px;
    box-shadow:0 10px 24px -18px rgba(33,31,27,.55);
}
.wj-slip::before { content:""; position:absolute; inset:4px; border:1px solid var(--line); pointer-events:none; }
.wj-slip-title { font-family:"WJ Brush", var(--kai); font-size:92px; line-height:1; letter-spacing:.14em; color:var(--ink); }
.wj-slip .gm-mark { font-size:13px; }
.wj-cover .gm-ver { align-self:center; padding:0; }
.st-key-cover { max-width:960px; margin:0 auto; }
.st-key-cover h3 { padding-top:10px; }
.st-key-keygate { max-width:560px; }

/* 世界观来源：做成书签页，不用圆点单选 */
.st-key-cover [data-testid="stRadio"], .st-key-cover [data-testid="stRadioGroup"] { width:100%; }
.st-key-cover [data-testid="stRadioGroup"] { gap:0; border-bottom:1px solid var(--line); }
.st-key-cover [data-testid="stRadioOption"] {
    margin:0 0 -1px; padding:9px 20px 8px; border-bottom:2px solid transparent; cursor:pointer; }
.st-key-cover [data-testid="stRadioOption"] > div > div > div:first-child { display:none; }
.st-key-cover [data-testid="stRadioOption"] p { font-family:var(--serif); font-size:16px; letter-spacing:.1em; color:var(--ink-2); }
.st-key-cover [data-testid="stRadioOption"]:hover p { color:var(--ink); }
.st-key-cover [data-testid="stRadioOption"]:has(input:checked) { border-bottom-color:var(--cinnabar); }
.st-key-cover [data-testid="stRadioOption"]:has(input:checked) p { color:var(--ink); font-weight:700; }
.st-key-cover [data-testid="stRadioOption"]:has(input:focus-visible) { background:rgba(162,58,44,.07); }
.st-key-world_form { background:var(--silk); border-color:var(--line) !important; border-radius:2px !important;
    padding:22px 24px 24px !important; box-shadow:0 14px 30px -26px rgba(33,31,27,.6); }
.st-key-world_preview { background:var(--silk); border:1.5px solid var(--frame); padding:24px 28px !important;
    outline:1px solid var(--line); outline-offset:-6px; }

/* —— 书页：页眉（地点 · 时辰 · 回目印）与正文同框 —— */
/* Streamlit 的 markdown 容器自带 -1rem 下边距，刚好抵消列间距：页眉与正文区无缝相接 */
.leaf-head {
    position:relative; display:flex; align-items:center; justify-content:space-between; gap:14px;
    background:var(--silk); border:1.5px solid var(--frame); border-bottom:none; padding:15px 40px 13px;
}
.leaf-head::before { content:""; position:absolute; inset:4px 4px 0; border:1px solid var(--line);
    border-bottom:none; pointer-events:none; }
.leaf-head::after { content:""; position:absolute; left:40px; right:40px; bottom:0; border-bottom:1px solid var(--line); }
.scene-strip { display:flex; flex-wrap:wrap; align-items:baseline; gap:2px 16px; min-width:0; }
.scene-place { font-family:var(--serif); color:var(--ink); font-size:18px; font-weight:700; letter-spacing:.08em; }
.scene-time { font-family:var(--kai); color:var(--ink-2); font-size:15px; }
.scene-time + .scene-time { padding-left:16px; border-left:1px solid var(--line); }
.gm-seal {
    flex:none; background:var(--cinnabar); color:#F8EFE6; font-family:var(--serif); font-weight:700;
    font-size:14px; line-height:1; letter-spacing:.2em; padding:6px 6px 6px 9px; border-radius:2px;
    box-shadow:inset 0 0 0 1.5px var(--cinnabar), inset 0 0 0 2.5px rgba(248,239,230,.5);
    transform:rotate(-1.5deg);
}

/* 只让消息区滚动，输入区始终在消息区外。 */
[data-testid="stLayoutWrapper"]:has(> [class*="st-key-chat_panel_"]) {
    position:relative; height:clamp(300px, calc(100dvh - 330px), 780px) !important; flex:0 0 auto;
}
[data-testid="stLayoutWrapper"]:has(> [class*="st-key-chat_panel_"])::after {
    content:""; position:absolute; inset:0 4px 4px; border:1px solid var(--line); border-top:none; pointer-events:none; }
[class*="st-key-chat_panel_"] {
    background:var(--silk); border:1.5px solid var(--frame); border-top:none; border-radius:0;
    padding:26px 40px 36px !important; scrollbar-gutter:stable; gap:24px !important;
}

/* —— 对话：旁白是正文，玩家行动用楷体与朱线标出 —— */
.message { max-width:42em; margin:0 auto; overflow-wrap:anywhere; }
.narrator-bubble { font-family:var(--serif); font-size:17px; line-height:2.05;
    color:var(--ink); letter-spacing:.02em; text-align:justify; }
.narrator-bubble p { margin:0 0 .85em; }
.narrator-bubble p:last-child { margin-bottom:0; }
.narrator-bubble strong { font-weight:700; color:#12100D; }
.narrator-bubble .ink-break { display:flex; align-items:center; justify-content:center; gap:10px;
    width:9em; margin:16px auto; color:var(--cinnabar); }
.narrator-bubble .ink-break::before, .narrator-bubble .ink-break::after {
    content:""; flex:1; border-top:1px solid var(--line); }
.narrator-bubble .ink-break i { width:5px; height:5px; background:currentColor; transform:rotate(45deg); }
.player-row { display:flex; justify-content:flex-end; }
.player-bubble { max-width:30em; font-family:var(--kai); font-size:16.5px; line-height:1.9; color:var(--ink-2);
    padding:2px 16px 2px 0; border-right:2px solid var(--cinnabar); text-align:right; }
.thinking-bubble { font-family:var(--kai); font-size:15px; color:var(--ink-3); padding:4px 0; }
.thinking-bubble::after { content:"…"; display:inline-block; animation:ink-breathe 1.6s ease-in-out infinite; }
@keyframes ink-breathe { 0%,100% { opacity:.25; } 50% { opacity:1; } }
/* 新落笔的一段旁白：墨迹由淡转浓（只作用于最后一条） */
[class*="st-key-chat_panel_"] > [data-testid="stElementContainer"]:last-child .narrator-bubble {
    animation:ink-in .9s ease-out both; }
@keyframes ink-in { from { opacity:0; filter:blur(3px); } to { opacity:1; filter:blur(0); } }
@media (prefers-reduced-motion: reduce) {
    .thinking-bubble::after,
    [class*="st-key-chat_panel_"] > [data-testid="stElementContainer"]:last-child .narrator-bubble { animation:none; }
}

/* —— 世界面板：册页式分节 —— */
[data-testid="stLayoutWrapper"]:has(> .st-key-world_panel) {
    height:clamp(400px, calc(100dvh - 190px), 900px) !important; flex:0 0 auto;
}
.st-key-world_panel { gap:44px !important; padding:6px 6px 24px 0; }
.gm-card { overflow-wrap:anywhere; }
.gm-card-title { font-family:var(--serif); color:var(--ink-2); font-size:14px; font-weight:700;
    letter-spacing:.3em; margin-bottom:12px; display:flex; align-items:center; gap:9px; white-space:nowrap; }
.gm-card-title::before { content:""; flex:none; width:7px; height:7px; background:var(--cinnabar); border-radius:1px; }
.gm-card-title::after { content:""; flex:1; border-top:1px solid var(--line); }
.pc-name { font-family:var(--serif); font-size:22px; font-weight:700; color:var(--ink); letter-spacing:.1em; line-height:1.4; }
.pc-role { font-family:var(--kai); font-size:14.5px; color:var(--ink-2); line-height:1.8; margin:2px 0 10px; }
.kv { display:grid; grid-template-columns:auto 1fr; gap:7px 12px; align-items:baseline; }
.kv .k { font-family:var(--kai); font-size:13.5px; color:var(--ink-3); white-space:nowrap; }
.kv .v { font-size:13.5px; color:var(--ink-2); line-height:1.75; min-width:0; }
.chip { display:inline-block; border:1px solid var(--line); background:var(--silk); border-radius:2px;
    padding:2px 9px; margin:2px 6px 2px 0; font-size:13px; line-height:1.6; color:var(--ink); font-family:var(--serif); }
.chip .qty { color:var(--ink-3); margin-left:5px; font-size:12px; }
.chip.tier { color:var(--tier); border-color:color-mix(in srgb, var(--tier) 42%, transparent); font-weight:700; }
.chip.tier-rainbow { border-image:linear-gradient(120deg,#b8402f,#b98416,#3f7350,#34618a,#6b4a8c) 1; }
.chip.state { background:transparent; border-style:dashed; color:var(--ink-2); font-family:var(--kai); }
.chip.state.is-bad { color:var(--cinnabar); border-color:rgba(162,58,44,.45); }
.char-row { display:flex; align-items:flex-start; gap:12px; padding:11px 0; border-bottom:1px dashed var(--line); }
.char-row:first-of-type { padding-top:0; }
.char-row:last-child { border-bottom:none; padding-bottom:0; }
.avatar { width:34px; height:34px; border-radius:50%; flex:none;
    background:radial-gradient(circle at 38% 32%, rgba(33,31,27,.04), rgba(33,31,27,.17));
    box-shadow:inset 0 0 0 1px var(--line-soft);
    color:var(--ink); display:flex; align-items:center; justify-content:center;
    font-family:var(--kai); font-size:17px; }
.char-body { min-width:0; flex:1; }
.char-head { display:flex; flex-wrap:wrap; align-items:baseline; gap:2px 10px; }
.char-name { font-family:var(--serif); font-size:15px; font-weight:700; color:var(--ink); letter-spacing:.04em; }
.char-tag { font-family:var(--kai); font-size:12.5px; color:var(--ink-3); }
.aff { display:inline-flex; align-items:center; gap:3px; font-family:var(--kai); font-size:12.5px; color:var(--ink-3); }
.aff i { width:6px; height:6px; border-radius:50%; border:1px solid var(--cinnabar); opacity:.55; }
.aff i.on { background:var(--cinnabar); opacity:1; }
.aff b { font-weight:400; margin-left:3px; }
.char-sub, .char-loc { font-size:13px; color:var(--ink-2); margin-top:3px; line-height:1.75; }
.char-loc { color:var(--ink-3); font-family:var(--kai); }
.goal { font-family:var(--serif); font-size:14.5px; line-height:1.85; color:var(--ink);
    padding:1px 0 1px 12px; border-left:2px solid var(--cinnabar); margin-bottom:10px; }
.goal small { display:block; font-family:var(--kai); font-size:12.5px; color:var(--ink-3); }
.bullet { font-size:13.5px; color:var(--ink-2); padding:3px 0; line-height:1.8; }
.bullet b { color:var(--ink); font-weight:700; font-family:var(--serif); }
.muted { font-size:13px; color:var(--ink-3); font-family:var(--kai); }

/* —— 控件 —— */
.stButton > button, .stDownloadButton > button {
    border-radius:2px; border-color:var(--line); background:rgba(248,244,234,.55); color:var(--ink);
    transition:border-color .15s, color .15s, background-color .15s; }
.stButton > button:hover, .stDownloadButton > button:hover { border-color:var(--cinnabar); color:var(--cinnabar); background:var(--silk); }
.stButton > button:focus-visible, .stDownloadButton > button:focus-visible { outline:2px solid var(--cinnabar); outline-offset:2px; }
.stButton > button[kind="primary"] { background:var(--cinnabar); border-color:var(--cinnabar); color:#F8EFE6; letter-spacing:.12em; }
.stButton > button[kind="primary"]:hover { background:var(--cinnabar-deep); border-color:var(--cinnabar-deep); color:#fff; }
.stButton > button:disabled { opacity:.5; }
div[data-testid="stTextInput"] input, div[data-testid="stTextArea"] textarea { background:var(--silk); color:var(--ink); font-family:var(--serif); }
.st-key-world_form div[data-testid="stTextInput"] input, .st-key-world_form div[data-testid="stTextArea"] textarea { background:#FCFAF4; }
[data-testid="stWidgetLabel"] p { font-family:var(--serif); letter-spacing:.04em; color:var(--ink-2); }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p { color:var(--ink-3) !important; opacity:1; }
.stTabs [role="tab"] p { font-family:var(--serif); font-size:16px; letter-spacing:.08em; }
.stTabs [data-baseweb="tab-list"] { gap:6px; }
[data-testid="stExpander"] details { border-color:var(--line); border-radius:2px; background:rgba(248,244,234,.5); }
[data-testid="stExpander"] summary p { font-family:var(--serif); letter-spacing:.06em; }
/* 提示条：石青 / 赭黄 / 朱砂三种批注色，去掉默认的色块 */
[data-testid="stAlert"] > div { background:var(--silk); border:1px solid var(--line); border-left:3px solid var(--azurite);
    border-radius:2px; color:var(--ink); }
[data-testid="stAlert"] p { font-family:var(--serif); color:var(--ink); line-height:1.8; }
[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]) > div { border-left-color:var(--ochre); }
[data-testid="stAlert"]:has([data-testid="stAlertContentError"]) > div { border-left-color:var(--cinnabar); }
hr { border-color:var(--line) !important; }
::-webkit-scrollbar { width:6px; height:6px; }
::-webkit-scrollbar-thumb { background:rgba(33,31,27,.2); border-radius:8px; }
::-webkit-scrollbar-thumb:hover { background:rgba(33,31,27,.34); }

/* —— 落笔处：输入框、继续、可选行动、安排时间 —— */
.st-key-composer { padding-top:4px; }
.st-key-composer [data-testid="stChatInput"] { border:1px solid var(--frame); border-radius:2px; background:var(--silk);
    box-shadow:0 8px 18px -16px rgba(33,31,27,.6); }
.st-key-composer [data-testid="stChatInput"]:focus-within { border-color:var(--cinnabar); box-shadow:0 0 0 3px rgba(162,58,44,.12); }
.st-key-composer [data-testid="stChatInput"], .st-key-composer [data-testid="stChatInput"] > div { background:var(--silk); }
.st-key-composer [data-testid="stChatInput"] textarea { font-family:var(--serif); font-size:16px; background:transparent; }
.st-key-composer [data-testid="stChatInput"] textarea::placeholder { font-family:var(--kai); color:var(--ink-3); opacity:1; }
.st-key-composer [data-testid="stChatInputSubmitButton"] { border-radius:2px; }
.st-key-composer [data-testid="stChatInputSubmitButton"]:not(:disabled) { background:var(--cinnabar); color:#F8EFE6; }
.st-key-block_resume button { border-color:rgba(162,58,44,.5); color:var(--cinnabar); background:rgba(162,58,44,.05);
    font-family:var(--serif); letter-spacing:.06em; }
.st-key-block_resume button:hover { background:rgba(162,58,44,.1); }
/* 可选行动：一行一条，像目录条目；悬停时朱砂点亮 */
.st-key-composer [class*="st-key-opt_"] button {
    justify-content:flex-start; text-align:left; border:none; border-bottom:1px dashed var(--line); border-radius:0;
    background:transparent; padding:8px 4px 8px 22px; position:relative; font-family:var(--serif); }
.st-key-composer [class*="st-key-opt_"] button::before { content:""; position:absolute; left:6px; top:50%;
    width:5px; height:5px; margin-top:-3px; background:var(--ink-3); transform:rotate(45deg); transition:background-color .15s; }
.st-key-composer [class*="st-key-opt_"] button:hover { background:rgba(162,58,44,.05); }
.st-key-composer [class*="st-key-opt_"] button:hover::before { background:var(--cinnabar); }
.st-key-composer [class*="st-key-opt_"] button > div, .st-key-composer [class*="st-key-opt_"] button span { justify-content:flex-start; }
.st-key-composer [class*="st-key-opt_"] button p { font-size:15px; text-align:left; }
.st-key-composer [class*="st-key-opt_"]:last-child button { border-bottom:none; }
.st-key-composer [data-testid="stExpanderDetails"] > [data-testid="stVerticalBlock"]:has([class*="st-key-opt_"]) { gap:0; }
.st-key-composer [data-testid="stExpanderDetails"]:has([class*="st-key-opt_"]) { padding-top:4px; padding-bottom:10px; }

/* —— 侧栏：存档 —— */
section[data-testid="stSidebar"] { background-color:#E1DAC8; background-image:var(--grain); border-right:1px solid var(--line); }
section[data-testid="stSidebar"] h3 { font-size:20px; letter-spacing:.3em; display:flex; align-items:center; gap:10px; }
section[data-testid="stSidebar"] h3::before { content:""; width:8px; height:8px; background:var(--cinnabar); border-radius:1px; }
section[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] { background:rgba(248,244,234,.6); border:1px dashed var(--line); border-radius:2px; }
/* 上传控件的英文提示换成中文（只改显示文字，不影响功能） */
[data-testid="stFileUploaderDropzone"] button [data-testid="stMarkdownContainer"] { display:none; }
[data-testid="stFileUploaderDropzone"] button > div > span::after { content:"选择文件"; font-family:var(--serif); }
[data-testid="stFileUploaderDropzoneInstructions"] > div { visibility:hidden; position:relative; }
[data-testid="stFileUploaderDropzoneInstructions"] > div::after { content:"可拖入文件，单个不超过 200MB"; visibility:visible;
    position:absolute; left:0; top:0; white-space:nowrap; font-size:13px; color:var(--ink-3); font-family:var(--kai); }

/* —— 构建主角：候选卡（卡面 + 底栏两个操作连成一体） —— */
.st-key-build_name { max-width:420px; }
[class*="st-key-pc_"] { gap:0 !important; background:var(--silk); border:1px solid var(--line); border-radius:2px;
    transition:box-shadow .15s, border-color .15s; }
[class*="st-key-pc_"]:hover { border-color:rgba(33,31,27,.32); }
[class*="st-key-pc_"]:has(.is-sel) { border-color:var(--cinnabar); box-shadow:0 0 0 1px var(--cinnabar), 0 10px 20px -18px rgba(162,58,44,.9); }
.pool-card { padding:12px 14px 10px; min-height:92px; }
.pool-top { display:flex; align-items:center; justify-content:space-between; gap:8px; }
.pool-name { font-family:var(--serif); font-weight:700; font-size:17px; color:var(--tier); letter-spacing:.06em; }
.pool-tier { flex:none; font-family:var(--serif); font-size:12px; font-weight:700; line-height:1; color:#F8EFE6;
    background:var(--tier); padding:4px 5px; border-radius:2px; }
.pool-card.tier-rainbow .pool-tier { background:linear-gradient(120deg,#b8402f,#b98416,#3f7350,#34618a,#6b4a8c); }
.pool-warn { color:var(--cinnabar); font-size:12px; margin-left:6px; }
.pool-desc { font-size:13.5px; color:var(--ink-2); margin-top:6px; line-height:1.7; }
.pool-cost { font-size:12.5px; color:var(--cinnabar); margin-top:4px; line-height:1.6; }
[class*="st-key-pc_"] [data-testid="stHorizontalBlock"] { gap:0; flex-wrap:nowrap; border-top:1px solid var(--line-soft); }
[class*="st-key-pc_"] [data-testid="stColumn"] { min-width:0 !important; flex:1 1 0 !important; }
[class*="st-key-pc_"] [data-testid="stColumn"] + [data-testid="stColumn"] { border-left:1px solid var(--line-soft); }
[class*="st-key-pc_"] .stButton > button { border:none; border-radius:0; background:transparent; min-height:38px; color:var(--ink-2); }
[class*="st-key-pc_"] .stButton > button p { font-size:13.5px; }
[class*="st-key-pc_"] .stButton > button:hover { background:rgba(162,58,44,.06); color:var(--cinnabar); }
[class*="st-key-pc_"] .stButton > button[kind="primary"] { background:var(--cinnabar); color:#F8EFE6; }
[class*="st-key-pc_"] .stButton > button[kind="primary"]:hover { background:var(--cinnabar-deep); color:#fff; }
[class*="st-key-pc_"] .stButton > button:focus-visible { outline-offset:-2px; }
[class*="st-key-remove_"] button { border-radius:999px; padding:2px 12px; min-height:30px; background:var(--silk); }
[class*="st-key-remove_"] button p { font-size:13.5px; }
.st-key-build_summary { background:var(--silk); border:1.5px solid var(--frame); padding:18px 22px !important;
    outline:1px solid var(--line); outline-offset:-6px; }
.sum-title { font-family:var(--serif); font-weight:700; font-size:15px; letter-spacing:.3em; color:var(--ink-2); margin-bottom:8px; }
.sum-row { display:grid; grid-template-columns:auto 1fr; gap:6px 14px; align-items:baseline; }

/* 空间不足时把世界信息移到对话下方，避免正文被挤成一条窄列。 */
@container (max-width:800px) {
    .st-key-game_layout > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] { flex-direction:column; }
    .st-key-game_layout > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { width:100%; flex:1 1 100%; }
    [data-testid="stLayoutWrapper"]:has(> .st-key-world_panel), .st-key-world_panel { height:auto !important; max-height:none !important; overflow:visible; }
}
@media (max-width:640px) {
    .wj-cover { align-items:flex-start; }
    .wj-slip { writing-mode:horizontal-tb; padding:14px 20px 12px; gap:14px; }
    .wj-slip-title { font-size:54px; letter-spacing:.08em; }
}
@media (max-width:600px) {
    .block-container { padding:3.2rem 1rem 1.5rem; }
    .gm-title, .gm-header.is-game .gm-title { font-size:34px; }
    .gm-page { font-size:18px; }
    .leaf-head { padding:12px 18px 10px; }
    .leaf-head::after { left:18px; right:18px; }
    .scene-place { font-size:16px; }
    .scene-time { font-size:14px; }
    .scene-time + .scene-time { padding-left:0; border-left:none; }
    .gm-save { display:none; }
    .st-key-cover h3 { padding-top:0; }
    [data-testid="stLayoutWrapper"]:has(> [class*="st-key-chat_panel_"]) { height:clamp(260px, calc(100dvh - 300px), 520px) !important; }
    [class*="st-key-chat_panel_"] { padding:18px 16px 24px !important; }
    .narrator-bubble { font-size:16px; line-height:1.95; }
    .player-bubble { max-width:94%; font-size:15.5px; }
    .st-key-world_form { padding:16px !important; }
}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------- 工具函数

def esc(text):
    return html.escape(str(text)).replace("\n", "<br>")


_BOLD = re.compile(r"\*\*(.+?)\*\*")


def story_html(text):
    """旁白正文：转义后只还原模型常用的少量 markdown（**加粗**、--- 分隔线）。"""
    blocks, para = [], []

    def flush():
        if para:
            blocks.append("<p>" + "<br>".join(para) + "</p>")
            para.clear()

    for line in str(text).split("\n"):
        stripped = line.strip()
        if not stripped:  # 空行 = 分段
            flush()
        elif stripped in ("---", "***", "___"):
            flush()
            blocks.append('<span class="ink-break"><i></i></span>')
        else:
            para.append(_BOLD.sub(r"<strong>\1</strong>", html.escape(line)))
    flush()
    return "".join(blocks)


_CN_DIGITS = "零一二三四五六七八九"


def cn_number(n):
    """回合数转中文数字（印章用）：12 → 十二，105 → 一百零五。"""
    n = int(n)
    if n < 10:
        return _CN_DIGITS[n]
    if n >= 10000:
        return str(n)
    parts, zero = [], False
    for value, unit in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
        d = n // value % 10
        if d:
            if zero:
                parts.append("零")
            parts.append(_CN_DIGITS[d] + unit)
            zero = False
        elif parts:
            zero = True
    s = "".join(parts)
    return s[1:] if s.startswith("一十") else s


def header_html(page=None, game_mode=False, note=""):
    """统一的顶部标题栏：毛笔「万界」+「文字冒险」方印、页面名、版本号（确认云端是否最新）。
    游戏中用窄一号的题头，右侧注明当前存档名；回目印章改盖在书页页眉（见 leaf_head_html）。"""
    sub = f'<span class="gm-page">{esc(page)}</span>' if page else ""
    right = f'<div class="gm-save">{esc(note)}</div>' if note else ""
    return (
        f'<div class="gm-header{" is-game" if game_mode else ""}"><div class="gm-brand">'
        '<span class="gm-title">万界</span><span class="gm-mark" aria-label="文字冒险">文字冒险</span>'
        f'{sub}<span class="gm-ver">{VERSION}</span>'
        f'</div>{right}</div>'
    )


def cover_html():
    """开局页的题签：竖排毛笔「万界」，像线装书封面左上角贴的那张签条。"""
    return (
        '<div class="wj-cover"><div class="wj-slip">'
        '<span class="wj-slip-title">万界</span>'
        '<span class="gm-mark" aria-label="文字冒险">文字冒险</span>'
        f'</div><span class="gm-ver">{VERSION}</span></div>'
    )


def leaf_head_html(state_data):
    """书页页眉：地点、时辰、已过多久，右侧盖本回的朱砂印。"""
    location = state_data.get("location") or {}
    turn = state_data.get("meta", {}).get("turn", 0)
    scene = f'<span class="scene-place">{esc(location.get("name") or "旅途中")}</span>'
    if state_data.get("time"):
        scene += f'<span class="scene-time">{esc(state_data["time"])}</span>'
    cal_days = (state_data.get("calendar") or {}).get("days") or 0
    if cal_days > 0:
        scene += f'<span class="scene-time">已过{esc(seasons.span_text(cal_days))}</span>'
    return (f'<div class="leaf-head"><div class="scene-strip">{scene}</div>'
            f'<div class="gm-seal" title="第 {turn} 回合">第{cn_number(turn)}回</div></div>')


def _secrets_api_key():
    """读取 Streamlit Cloud Secrets 中的 API Key（部署到云端时用）。"""
    try:
        s = st.secrets
        key = None
        for reader in (lambda: s.get("DEEPSEEK_API_KEY"),
                       lambda: s["DEEPSEEK_API_KEY"],
                       lambda: getattr(s, "DEEPSEEK_API_KEY", None)):
            try:
                key = reader()
            except Exception:
                continue
            if key:
                break
        if key and str(key).strip():
            return str(key).strip()
    except Exception:
        pass
    return None


def ensure_llm():
    if "llm" not in st.session_state:
        key = _secrets_api_key() or load_api_key()
        if key:
            st.session_state.llm = LLMClient(api_key=key, model=load_model() or "deepseek-chat", base_url=BASE_URL)
        else:
            st.session_state.llm = None
    return st.session_state.llm


def affinity_label(info, turn):
    aff = info.get("affinity")
    if not isinstance(aff, (int, float)):
        return None
    eff = dynamics.effective_affinity(info, turn)
    if eff >= 70:
        return "好感=高"
    if eff >= 40:
        return "好感=中"
    if eff >= 15:
        return "好感=低"
    return "好感=陌生"


def render_log(log):
    """按发生顺序显示消息；滚动由外层消息容器负责。"""
    for m in log:
        role = m.get("role")
        raw = m.get("content", "")
        if role == "player":
            body = (f'<div class="player-row"><div class="player-bubble" aria-label="你的行动">'
                    f'{esc(raw)}</div></div>')
        elif role == "thinking":
            body = f'<div class="thinking-bubble" role="status">{esc(raw).rstrip("…")}</div>'
        else:
            body = f'<div class="narrator-bubble">{story_html(raw)}</div>'
        st.markdown(f'<div class="message">{body}</div>', unsafe_allow_html=True)


def render_card(title, content):
    st.markdown(f'<section class="gm-card"><div class="gm-card-title">{esc(title)}</div>'
                f'{content}</section>', unsafe_allow_html=True)


# 品级在界面上的颜色：取国画矿物颜料的色相（石绿、石青、青莲、泥金、朱砂），在素绢底上都够深。
TIER_INK = {"白": "#6B665C", "绿": "#3B6E4C", "蓝": "#33608A", "紫": "#69488A",
            "金": "#8A6010", "红": "#A8321F", "炫彩": "#7A3F8F"}
_AFFINITY_LEVELS = {"好感=陌生": (0, "陌生"), "好感=低": (1, "疏淡"), "好感=中": (2, "友善"), "好感=高": (3, "亲近")}
_BAD_BANDS = ("重伤", "受伤", "轻伤", "死亡")


def tier_chip(entry):
    """天赋/体质/金手指的小签：按品级着色，悬停显示说明。"""
    if isinstance(entry, dict):
        name, tier, desc = entry.get("name"), entry.get("tier"), entry.get("description") or ""
    else:
        name, tier, desc = str(entry), None, ""
    if not name:
        return ""
    if tier not in TIER_INK:
        return f'<span class="chip" title="{esc(desc)}">{esc(name)}</span>'
    extra = " tier-rainbow" if tier == "炫彩" else ""
    tip = f"{tier}品" + (f"：{desc}" if desc else "")
    return (f'<span class="chip tier{extra}" style="--tier:{TIER_INK[tier]};" title="{esc(tip)}">'
            f'{esc(name)}</span>')


def affinity_html(label):
    """把「好感=高」这类定性标签画成三枚圆点 + 一个词；仍然只有四档，不暴露数值。"""
    level, word = _AFFINITY_LEVELS.get(label, (0, "陌生"))
    dots = "".join(f'<i class="{"on" if i < level else ""}"></i>' for i in range(3))
    return f'<span class="aff" title="{esc(label.replace("=", "："))}">{dots}<b>{word}</b></span>'


def flag_text(value):
    """剧情标记的值：布尔值写成「是 / 尚未」，其余原样显示。"""
    if value is True:
        return "是"
    if value is False:
        return "尚未"
    return str(value)


def render_world_panel(game):
    d = game.state.data
    turn = d.get("meta", {}).get("turn", 0)

    # —— 主角 ——
    p = d.get("player", {}) or {}
    name = p.get("name") or "你"
    role = p.get("role_description") or ""
    html_bits = [f'<div class="pc-name">{esc(name)}</div>']
    if role:
        html_bits.append(f'<div class="pc-role">{esc(role)}</div>')
    rows = []
    talents = "".join(tier_chip(t) for t in (p.get("talents") or []))
    rows.append(("天赋", talents or '<span class="muted">无</span>'))
    physique = p.get("physique") or {}
    gfs = p.get("golden_fingers") or ([p["golden_finger"]] if p.get("golden_finger") else [])
    gf_chips = "".join(tier_chip(g) for g in gfs if isinstance(g, dict))
    if physique.get("name") or gf_chips:
        rows.append(("体质", tier_chip(physique) or '<span class="muted">无</span>'))
        rows.append(("金手指", gf_chips or '<span class="muted">无</span>'))
    for protection in game.state.protection_summary():
        rows.append(("保命", esc(protection)))
    # 身体状况只给定性分档（健康时不占地方），后面跟模型记录的状态
    band = game.state._hp_band() if hasattr(game.state, "_hp_band") else None
    status = list(p.get("status_effects") or [])
    if band and band != "健康" and band not in status:
        status.insert(0, band)
    if status:
        rows.append(("状态", "".join(
            f'<span class="chip state{" is-bad" if s in _BAD_BANDS else ""}">{esc(s)}</span>' for s in status)))
    html_bits.append('<div class="kv">' + "".join(
        f'<span class="k">{k}</span><span class="v">{v}</span>' for k, v in rows) + "</div>")
    render_card("你的角色", "".join(html_bits))

    # —— 遇见的人 ——
    chars = d.get("characters") or {}
    if chars:
        rows = []
        for name, info in chars.items():
            avatar = name[0] if name else "?"
            head = f"<span class='char-name'>{esc(name)}</span>"
            aff = affinity_label(info, turn)
            if aff:
                head += affinity_html(aff)
            status = info.get("status")
            if status and status != "存活":  # 「存活」是默认状态，不必每个人都写一遍
                head += f"<span class='char-tag'>{esc(status)}</span>"
            desc = f"<div class='char-sub'>{esc(info['description'])}</div>" if info.get("description") else ""
            loc_line = f"<div class='char-loc'>在 {esc(info.get('location', ''))}</div>" if info.get("location") else ""
            rows.append(
                f"<div class='char-row'><div class='avatar'>{esc(avatar)}</div>"
                f"<div class='char-body'><div class='char-head'>{head}</div>{desc}{loc_line}</div></div>"
            )
        render_card("遇见的人", "".join(rows))
    else:
        render_card("遇见的人", '<div class="muted">暂未遇见其他角色</div>')

    # —— 行囊（小签平铺）——
    inv = d.get("inventory") or []
    if inv:
        chips = "".join(
            f'<span class="chip" title="{esc(i.get("description") or "")}">{esc(i.get("name"))}'
            f'<span class="qty">×{esc(i.get("quantity", 1))}</span></span>'
            for i in inv
        )
        render_card("行囊", chips)
    else:
        render_card("行囊", '<div class="muted">暂无物品</div>')

    # —— 旅途线索：当前目标单独突出，其余剧情标记逐条列出 ——
    flags = d.get("plot_flags") or {}
    goal = d.get("current_goal") or ""
    bullets = []
    if goal:
        bullets.append(f'<div class="goal"><small>当前目标</small>{esc(goal)}</div>')
    for k, v in flags.items():
        bullets.append(f'<div class="bullet"><b>{esc(k)}</b>：{esc(flag_text(v))}</div>')
    if bullets:
        render_card("旅途线索", "".join(bullets))
    else:
        render_card("旅途线索", '<div class="muted">故事刚刚开始</div>')


# 主持人“思考链”文案（等待模型回复时在旁白气泡里随机展示）
THINKING_PHRASES = [
    "正在推演剧情走向…",
    "正在斟酌角色反应…",
    "正在编织对白…",
    "正在铺陈氛围…",
    "正在权衡因果…",
    "正在翻找线索…",
    "正在酝酿转折…",
    "正在推敲细节…",
]


def random_think():
    return random.choice(THINKING_PHRASES)


def session_slot():
    """本会话的临时自动存档槽（不同玩家互不覆盖，且不会出现在「继续存档」列表里）。"""
    if "session_slot" not in st.session_state:
        st.session_state.session_slot = "临时-" + str(random.randint(100000, 999999))
    return st.session_state.session_slot


def _save_to_named_slot(name):
    """把当前游戏显式保存到一个命名槽，成功返回 True。"""
    key = (name or "").strip()
    if not key:
        return False
    game = st.session_state.game
    game.state.save_key = key
    game.memory.save_key = key
    game.save()
    st.session_state.current_slot = key
    return True


def _load_named_slot(key):
    """从命名槽读档并切到游戏界面。"""
    try:
        state = WorldState.load(key)
    except SaveCorruptError as e:
        st.error(str(e) + "。可以尝试读取其他存档，或用「从设备导入」恢复之前导出的存档。")
        return
    if state is None:
        st.error("该存档不存在或已失效。")
        return
    memory = Memory.load(key)
    game = Game(state, memory, st.session_state.llm)
    st.session_state.game = game
    st.session_state.log = [{"role": "narrator", "content": "（已读取存档）\n" + state.to_player_summary()}]
    st.session_state.current_slot = key
    st.session_state.character_step = False
    st.rerun()


def _stop_world():
    """停止当前世界，清空游戏与构建状态，返回世界观生成界面。"""
    for k in ("game", "log", "current_slot", "pending_input", "character_step", "character_pool",
              "previous_character_pool",
              "pending_block", "failed_block", "block_plan", "block_len", "block_seclusion",
              "turn_error", "failed_input",
              "ask_stop", "saving", "stop_after_save", "template",
              "talent_hand", "physique_hand", "gf_hand", "talent_lock", "physique_lock", "gf_lock",
              "talent_sel", "physique_sel", "gf_sel"):
        st.session_state.pop(k, None)
    st.rerun()


def load_into_session(state_data, memory_dict, save_key=""):
    """把世界状态 + 记忆数据装入当前会话并切到游戏界面（用于导入存档）。"""
    state = WorldState(state_data, save_key=save_key)
    memory = Memory.from_dict(memory_dict, save_key=save_key)
    game = Game(state, memory, st.session_state.llm)
    st.session_state.game = game
    st.session_state.log = []
    st.session_state.log.append({
        "role": "narrator",
        "content": "（已读取存档）\n" + state.to_player_summary(),
    })
    st.session_state.character_step = False
    st.rerun()


def start_game(template):
    save_key = session_slot()
    state = WorldState(save_key=save_key)
    state.apply_template(template)
    game = Game(state, Memory(save_key=save_key), st.session_state.llm)
    loading = world_gen.world_loading_phrase(template)
    with st.spinner(loading):
        try:
            opening = game.start()
        except Exception as e:
            # 开局失败：留在构建页，已选的天赋等都还在，可以直接再点一次开始。
            st.error("开局失败，请稍后再试。" + (str(e) if isinstance(e, LLMError) else ""))
            return
    st.session_state.game = game
    st.session_state.log = []
    st.session_state.log.append({"role": "narrator", "content": opening})
    st.session_state.current_slot = None
    st.session_state.pop("preview", None)
    st.session_state.pop("template", None)
    st.session_state.character_step = False
    st.rerun()


def render_save_sidebar():
    """侧边栏：显式保存（起名+确认）/ 继续存档列表 / 导出导入。"""
    with st.sidebar:
        st.markdown("### 存档")

        game = st.session_state.get("game")
        cur = st.session_state.get("current_slot")

        # 显式保存：点按钮 → 起名 → 确认
        if game is not None:
            if not st.session_state.get("saving"):
                cap = "当前存档：" + (cur or "未命名")
                st.caption(cap)
                if st.button("保存游戏", icon=":material/bookmark:", type="primary", use_container_width=True):
                    st.session_state.saving = True
                    st.rerun()
            else:
                name = st.text_input("给存档起个名字（如 111 / 222）", key="save_name_draft")
                c1, c2 = st.columns(2)
                if c1.button("确认保存", type="primary", use_container_width=True):
                    if _save_to_named_slot(name):
                        st.session_state.saving = False
                        if st.session_state.get("stop_after_save"):
                            _stop_world()
                        else:
                            st.rerun()
                    else:
                        st.warning("名字不能为空")
                if c2.button("取消", use_container_width=True):
                    st.session_state.saving = False
                    st.rerun()

        # 导出 / 导入（跨设备持久化）
        if game is not None:
            data = save_bundle.bundle_json(game.state, game.memory)
            st.download_button(
                "导出到设备",
                icon=":material/download:",
                data=data.encode("utf-8"),
                file_name="存档_" + (cur or "未命名") + ".json",
                mime="application/json",
                use_container_width=True,
            )
        up = st.file_uploader("从设备导入", type=["json"], key="import_save")
        if up is not None:
            try:
                state_data, memory_dict = save_bundle.load_bundle(up.getvalue())
            except Exception as e:
                st.error("导入失败：" + str(e))
            else:
                st.session_state.pop("import_save", None)
                load_into_session(state_data, memory_dict, session_slot())

        # 继续存档：列出所有已命名的存档（排除会话临时槽）
        st.divider()
        st.markdown("**继续存档**")
        slots = [s for s in list_save_slots() if not (s["name"] or s["key"]).startswith("临时-")]
        if slots:
            for s in slots:
                label = s["name"] or s["key"]
                if st.button(label, icon=":material/menu_book:", key="load_" + s["key"], use_container_width=True):
                    _load_named_slot(s["key"])
        else:
            st.caption("暂无已命名的存档")

        st.caption("云端磁盘是临时的，跨设备请用「导出/导入」。")

        # 停止世界
        if game is not None:
            st.divider()
            if not st.session_state.get("ask_stop"):
                if st.button("停止世界", icon=":material/stop_circle:", use_container_width=True):
                    st.session_state.ask_stop = True
                    st.rerun()
            else:
                st.warning("确定停止当前世界吗？")
                c1, c2, c3 = st.columns(3)
                if c1.button("保存后停止", use_container_width=True):
                    st.session_state.stop_after_save = True
                    st.session_state.saving = True
                    st.rerun()
                if c2.button("不保存", use_container_width=True):
                    _stop_world()
                if c3.button("取消", use_container_width=True):
                    st.session_state.ask_stop = False
                    st.rerun()


def start_build(template):
    """世界观已定，进入主角构建步骤（重置候选池与选择状态）。"""
    st.session_state.template = template
    st.session_state.character_step = True
    st.session_state.character_pool = None
    st.session_state.pop("previous_character_pool", None)
    for k in ("talent_hand", "physique_hand", "gf_hand", "talent_lock", "physique_lock", "gf_lock",
              "talent_sel", "physique_sel", "gf_sel"):
        st.session_state.pop(k, None)
    st.rerun()


def _ensure_hands(pool):
    """确保每个类别的 9 张手牌已生成。"""
    mapping = {"talent": "talents", "physique": "physiques", "gf": "golden_fingers"}
    for cat, key in mapping.items():
        hand_key = cat + "_hand"
        items = pool.get(key, [])
        valid_names = {item["name"] for item in items}
        hand = st.session_state.get(hand_key) or []
        hand_names = [item.get("name") if isinstance(item, dict) else None for item in hand]
        if (len(hand) != min(character_builder.HAND_SIZE, len(items))
                or len(set(hand_names)) != len(hand_names)
                or any(name not in valid_names for name in hand_names)):
            st.session_state[hand_key] = character_builder.sample_hand(items, previous=hand)
        if st.session_state.get(cat + "_lock") not in valid_names:
            st.session_state.pop(cat + "_lock", None)


def _toggle_lock(cat, name):
    lock_key = cat + "_lock"
    st.session_state[lock_key] = None if st.session_state.get(lock_key) == name else name
    st.rerun()


def _toggle_select(cat, name, is_multi, max_sel):
    sel_key = cat + "_sel"
    if is_multi:
        cur = list(st.session_state.get(sel_key) or [])
        if name in cur:
            cur.remove(name)
        else:
            if len(cur) >= max_sel:
                st.warning(f"最多选 {max_sel} 个。")
                return
            cur.append(name)
        st.session_state[sel_key] = cur
    else:
        st.session_state[sel_key] = None if st.session_state.get(sel_key) == name else name
    st.rerun()


def _render_pool_card(cat, item, idx, locked, sel, is_multi, max_sel):
    name = item["name"]
    tier = item.get("tier") or "蓝"
    style = character_builder.TIER_STYLES.get(tier, character_builder.TIER_STYLES["蓝"])
    is_locked = (locked == name)
    is_sel = (name in sel) if is_multi else (sel == name)

    classes = "pool-card"
    if tier == "炫彩":
        classes += " tier-rainbow"
    if is_sel:
        classes += " is-sel"
    warn = '<span class="pool-warn" title="有代价">⚠</span>' if item.get("drawback") else ""

    # 说明直接写在卡面上（每条约 25 字，不必再点开）；有代价时跟在后面
    desc = item.get("description") or ""
    drawback = item.get("drawback") or ""
    body = f'<div class="pool-desc">{esc(desc)}</div>' if desc else ""
    if drawback:
        body += f'<div class="pool-cost">代价：{esc(drawback)}</div>'

    # 卡面与底栏的「锁定 / 选择」放进同一个容器，样式上连成一张卡
    with st.container(key=f"pc_{cat}_{idx}"):
        st.markdown(
            f'<div class="{classes}" style="--tier:{TIER_INK.get(tier, style["fg"])};">'
            f'<div class="pool-top"><span class="pool-name">{esc(name)}{warn}</span>'
            f'<span class="pool-tier" title="品级：{esc(tier)}">{esc(tier[-1])}</span></div>'
            f'{body}</div>',
            unsafe_allow_html=True,
        )
        b1, b2 = st.columns(2)
        with b1:
            if st.button("已锁" if is_locked else "锁定",
                         icon=":material/lock:" if is_locked else ":material/lock_open:",
                         key=f"lock_{cat}_{name}", use_container_width=True):
                _toggle_lock(cat, name)
        with b2:
            if st.button("已选" if is_sel else "选择", icon=":material/check:" if is_sel else None,
                         type="primary" if is_sel else "secondary",
                         key=f"sel_{cat}_{name}", use_container_width=True):
                _toggle_select(cat, name, is_multi, max_sel)


def _render_pool_picker(cat, title, pool_items, is_multi, max_sel, allow_none=False):
    hand_key = cat + "_hand"
    lock_key = cat + "_lock"
    sel_key = cat + "_sel"
    locked = st.session_state.get(lock_key)

    c1, c2 = st.columns([1, 3], vertical_alignment="center")
    with c1:
        if st.button("换一组", icon=":material/casino:", key=f"roll_{cat}", use_container_width=True):
            st.session_state[hand_key] = character_builder.sample_hand(
                pool_items, character_builder.HAND_SIZE, locked,
                previous=st.session_state.get(hand_key))
            st.rerun()
    with c2:
        shown = min(character_builder.HAND_SIZE, len(pool_items))
        lock_tip = f"已锁定「{locked}」，换一组时保留。" if locked else "可锁定 1 个，换一组时保留。"
        st.caption(f"{title}共 {len(pool_items)} 个候选，每次展示 {shown} 个。{lock_tip}已选的不会被换掉。")

    selected = st.session_state.get(sel_key)
    selected = selected if isinstance(selected, list) else ([selected] if selected else [])
    if selected:
        with st.container(horizontal=True, horizontal_alignment="left", vertical_alignment="center", gap="small"):
            st.caption("已选（点击可移除）", width="content")
            for name in selected:
                if st.button(name, icon=":material/close:", key=f"remove_{cat}_{name}",
                             help="移除这一项"):
                    _toggle_select(cat, name, is_multi, max_sel)

    if allow_none:
        if st.button("不携带金手指", icon=":material/block:", key=f"none_{cat}", use_container_width=True):
            st.session_state[sel_key] = []
            st.rerun()

    hand = st.session_state.get(hand_key) or []
    sel = st.session_state.get(sel_key)
    if is_multi:
        sel = sel if isinstance(sel, list) else []
    for r in range(3):
        cols = st.columns(3)
        for c in range(3):
            idx = r * 3 + c
            if idx >= len(hand):
                continue
            with cols[c]:
                _render_pool_card(cat, hand[idx], idx, locked, sel, is_multi, max_sel)


# 一键天命的品级权重：偏向紫/金，红和炫彩也有机会出。
_AUTO_TIER_WEIGHT = {"白": 1, "绿": 2, "蓝": 3, "紫": 3.5, "金": 3, "红": 1.5, "炫彩": 0.6}


def _weighted_pick(items, k):
    left = [x for x in items if isinstance(x, dict) and x.get("name")]
    picked = []
    for _ in range(min(k, len(left))):
        choice = random.choices(left, weights=[_AUTO_TIER_WEIGHT.get(x.get("tier"), 1) for x in left])[0]
        picked.append(choice)
        left.remove(choice)
    return picked


def _auto_build(pool):
    """一键天命：随机配 2~3 个天赋 + 1 体质 + 1 金手指，写入已选（不改动锁定与当前展示的候选）。"""
    st.session_state.talent_sel = [x["name"] for x in _weighted_pick(
        pool.get("talents", []), random.randint(2, character_builder.MAX_TALENTS))]
    physique = _weighted_pick(pool.get("physiques", []), 1)
    st.session_state.physique_sel = physique[0]["name"] if physique else None
    st.session_state.gf_sel = [x["name"] for x in _weighted_pick(pool.get("golden_fingers", []), 1)]
    st.rerun()


def render_character_build():
    """主角构建页：按世界观生成的候选池 + roll/锁定/选定 + 自定义。"""
    template = st.session_state.get("template")
    if not template:
        st.session_state.character_step = False
        st.rerun()

    # 分类别生成，防止一次输出过多候选被截断。
    if not st.session_state.get("character_pool"):
        with st.spinner("正在按世界观生成候选天赋/体质/金手指…"):
            st.session_state.character_pool = character_builder.generate_pool(
                st.session_state.llm, template,
                previous_pool=st.session_state.pop("previous_character_pool", None))
    pool = st.session_state.character_pool
    if (pool.get("_meta") or {}).get("version") != character_builder.POOL_VERSION:
        pool = character_builder.prepare_pool(pool)
        st.session_state.character_pool = pool

    st.markdown(header_html(page="构建主角"), unsafe_allow_html=True)
    w = template.get("world") or {}
    st.caption(f"为「{w.get('name') or '这个世界'}」挑选主角的天赋、体质与金手指，锁定心仪的再换一组，选好后进入故事。")
    if any(source != "ai" for source in pool.get("_meta", {}).get("sources", {}).values()):
        st.caption("部分候选使用了备用选项。可继续换一组，或重新生成候选池。")
    if w.get("background"):
        with st.expander("查看世界观"):
            st.write(w["background"])

    # 主角姓名（开局后不再反复问姓名）
    with st.container(key="build_name"):
        player_name = st.text_input("你的姓名 / 称呼", key="player_name", placeholder="可留空，将用「你」代替")

    with st.container(horizontal=True, gap="small"):
        if st.button("一键天命", icon=":material/auto_awesome:", type="primary", key="auto_build",
                     help="从候选池里随机配一套天赋、体质和金手指（高品级更容易出现），可以再手动调整"):
            _auto_build(pool)
        if st.button("重新生成候选池", icon=":material/refresh:"):
            st.session_state.previous_character_pool = pool
            st.session_state.character_pool = None
            for k in ("talent_hand", "physique_hand", "gf_hand", "talent_lock", "physique_lock", "gf_lock",
                      "talent_sel", "physique_sel", "gf_sel"):
                st.session_state.pop(k, None)
            st.rerun()

    _ensure_hands(pool)

    tab_t, tab_p, tab_g, tab_c = st.tabs(["天赋（选 1–3）", "体质（选 1）", "金手指（选 1–3）", "自定义"])
    with tab_t:
        _render_pool_picker("talent", "天赋", pool["talents"], is_multi=True, max_sel=character_builder.MAX_TALENTS)
    with tab_p:
        _render_pool_picker("physique", "体质", pool["physiques"], is_multi=False, max_sel=1)
    with tab_g:
        _render_pool_picker("gf", "金手指", pool["golden_fingers"], is_multi=True, max_sel=3, allow_none=True)
    with tab_c:
        custom_talent = st.text_input("自定义天赋名", key="custom_talent", placeholder="叠加到已选天赋（可空）")
        custom_physique = st.text_input("自定义体质名", key="custom_physique", placeholder="覆盖上面的体质选择")
        custom_gf = st.text_input("自定义金手指名", key="custom_gf", placeholder="覆盖上面的金手指选择")

    # 汇总
    talents = list(st.session_state.get("talent_sel") or [])
    if (custom_talent or "").strip():
        talents.append(custom_talent.strip())
        talents = talents[: character_builder.MAX_TALENTS]
    physique = (custom_physique or "").strip() or st.session_state.get("physique_sel") or "均衡之躯"

    # 金手指：1~3 个（自定义覆盖）
    gf_list = list(st.session_state.get("gf_sel") or [])
    if (custom_gf or "").strip():
        gf_list = [custom_gf.strip()]
    gf_list = gf_list[:3]

    # 从池子里取等级、描述和代价（用于写入状态）
    all_pool_items = pool["talents"] + pool["physiques"] + pool["golden_fingers"]
    name_to_item = {x["name"]: x for x in all_pool_items}
    tier_map, desc_map, drawback_map = {}, {}, {}
    for n in list(talents) + ([physique] if physique else []) + list(gf_list):
        if n and n in name_to_item:
            tier_map[n] = name_to_item[n]["tier"]
            desc_map[n] = name_to_item[n]["description"]
            drawback_map[n] = name_to_item[n].get("drawback", "")

    # 你的选择：按品级着色的小签，一眼看清这套搭配
    def _chips(names):
        return "".join(tier_chip({"name": n, "tier": tier_map.get(n), "description": desc_map.get(n, "")})
                       for n in names) or '<span class="muted">未选</span>'

    with st.container(key="build_summary"):
        st.markdown(
            '<div class="sum-title">你的选择</div><div class="sum-row">'
            + (f'<span class="k">姓名</span><span class="v">{esc(player_name.strip())}</span>' if player_name.strip() else "")
            + f'<span class="k">天赋</span><span class="v">{_chips(talents)}</span>'
            + f'<span class="k">体质</span><span class="v">{_chips([physique])}</span>'
            + f'<span class="k">金手指</span><span class="v">{_chips(gf_list) if gf_list else "不携带"}</span>'
            + "</div>", unsafe_allow_html=True)
    st.caption("品级：白 < 绿 < 蓝 < 紫 < 金 < 红 < 炫彩。自定义项没有固定品级，由主持人按名字发挥。")

    c1, c2, _ = st.columns([1, 1, 2])
    with c1:
        if st.button("确认开始", type="primary", use_container_width=True):
            start_game(character_builder.build_player(
                template, talents, physique, gf_list,
                descriptions=desc_map, tiers=tier_map, drawbacks=drawback_map,
                player_name=player_name))
    with c2:
        if st.button("返回世界观", use_container_width=True):
            st.session_state.character_step = False
            st.session_state.pop("template", None)
            st.rerun()


def _busy():
    """是否有待处理的行动/时间安排：期间禁用输入与行动控件，避免并发发起。"""
    return bool(st.session_state.get("pending_input") or st.session_state.get("pending_block"))


def handle_input(text):
    """先立即展示玩家消息，再在下一轮渲染里显示“思考链”并调用模型。"""
    if st.session_state.game.is_over or _busy():
        return
    st.session_state.pop("turn_error", None)
    st.session_state.pop("failed_input", None)
    st.session_state.pop("failed_block", None)  # 新行动开始后，旧的失败安排不再是“重试”的对象
    st.session_state.log.append({"role": "player", "content": text})
    st.session_state.pending_input = text
    st.rerun()


def handle_block(plan, days, seclusion, resume=False):
    """岁月模式：先立即展示玩家消息，再在下一轮渲染里结算这段时间。"""
    if st.session_state.game.is_over or _busy():
        return
    st.session_state.pop("turn_error", None)
    st.session_state.pop("failed_block", None)
    st.session_state.pop("failed_input", None)
    st.session_state.log.append({
        "role": "player",
        "content": ("继续：" + plan) if resume else seasons.plan_text(plan, days, seclusion),
    })
    st.session_state.pending_block = {"plan": plan, "days": days, "seclusion": seclusion, "resume": resume}
    st.rerun()


# ---------------------------------------------------------------- 主流程

llm = ensure_llm()

# 页面热更新时，把仍在会话里的旧Game实例迁到新逻辑，保留进度与聊天。
previous_game = st.session_state.get("game")
if previous_game is not None and not isinstance(previous_game, Game):
    updated_state = WorldState(previous_game.state.data, save_key=previous_game.state.save_key)
    updated_memory = Memory.from_dict(previous_game.memory.to_dict(), save_key=previous_game.memory.save_key)
    st.session_state.game = Game(updated_state, updated_memory, llm, rules=previous_game.rules)

# 无 Key：先输入 Key
if llm is None:
    st.markdown(header_html(), unsafe_allow_html=True)
    with st.container(key="keygate"):
        st.caption("需要 DeepSeek API Key 才能运行。")
        key = st.text_input("DeepSeek API Key", type="password", placeholder="sk-...")
    if key and key.strip():
        save_api_key(key.strip())
        st.session_state.llm = LLMClient(api_key=key.strip(), model=load_model() or "deepseek-chat", base_url=BASE_URL)
        st.rerun()
    st.stop()

# 侧边栏：存档管理（存档名 + 导出/导入），所有状态都可用
render_save_sidebar()

# 尚未开始游戏 → 主角构建页 / 世界观设置页
if "game" not in st.session_state or st.session_state.game is None:
    if st.session_state.get("character_step"):
        render_character_build()
        st.stop()

    # 封面式布局：左边一张竖排题签，右边选世界观来源并填写
    with st.container(key="cover"):
        slip_col, form_col = st.columns([1, 4.4], gap="large")
        with slip_col:
            st.markdown(cover_html(), unsafe_allow_html=True)
        with form_col:
            st.markdown("### 开始新游戏")
            st.caption("选择世界观来源，然后进入故事。")

            mode = st.radio("世界观来源", ["手动输入", "AI 生成", "上传文件"], horizontal=True,
                            label_visibility="collapsed")
            with st.container(border=True, key="world_form"):
                if mode == "手动输入":
                    bg = st.text_area("世界观 / 背景", height=90)
                    role = st.text_input("你扮演的角色")
                    init = st.text_area("初始情境", height=70)
                    style = st.text_input("主持风格（可留空）")
                    if st.button("开始游戏", type="primary", use_container_width=True):
                        if not bg and not init:
                            st.warning("至少填写世界观/背景或初始情境。")
                        else:
                            start_build({
                                "world": {"name": "", "background": bg, "rules": "", "style": style},
                                "player": {"name": "", "role_description": role, "attributes": {}, "status_effects": []},
                                "initial_situation": init,
                                "location": {"name": "", "description": ""}, "time": "",
                                "characters": {}, "inventory": [], "plot_flags": {}, "current_goal": "",
                            })

                elif mode == "AI 生成":
                    theme = st.text_input("主题 / 类型（可留空）", placeholder="修仙 / 赛博朋克 / 悬疑…")
                    keywords = st.text_input("关键词 / 要求（可留空）")
                    role_hint = st.text_input("想扮演的角色类型（可留空）")
                    if st.button("生成世界观", use_container_width=True):
                        with st.spinner("正在生成…"):
                            try:
                                t = world_gen.generate_world(llm, theme, keywords, role_hint)
                            except Exception as e:
                                st.error(str(e))
                                t = None
                        if t:
                            st.session_state.preview = t

                else:  # 上传文件
                    up = st.file_uploader("上传 .json / .txt / .md", type=["json", "txt", "md"])
                    if up is not None:
                        raw = up.read().decode("utf-8")
                        try:
                            if up.name.lower().endswith(".json"):
                                t = world_gen.load_template_from_json_text(raw)
                            else:
                                with st.spinner("正在将文本结构化为世界观…"):
                                    t = world_gen.structure_text(llm, raw, "")
                            st.session_state.preview = t
                        except Exception as e:
                            st.error(str(e))

        # 预览（生成 / 上传后显示在下方，全宽）
        if "preview" in st.session_state:
            with st.container(key="world_preview"):
                st.markdown("**世界观预览**")
                st.markdown(st.session_state.preview.get("world", {}).get("background", "（无背景）"))
                st.markdown("```\n" + world_gen.render_preview(st.session_state.preview) + "\n```")
            if st.button("采用并开始", type="primary", use_container_width=True):
                start_build(st.session_state.preview)

    st.stop()

# ============ 游戏中：三区布局 ============
game = st.session_state.game
log = st.session_state.log
pending = st.session_state.get("pending_input")
pending_blk = st.session_state.get("pending_block")
busy = bool(pending or pending_blk)

# 顶部标题栏
st.markdown(header_html(game_mode=True, note="存档：" + (st.session_state.get("current_slot") or "未命名")),
            unsafe_allow_html=True)

# 待处理输入时，追加一个“思考链”占位气泡（下一轮渲染会先显示它）
show_log = list(log)
if busy:
    show_log.append({"role": "thinking", "content": random_think()})

with st.container(key="game_layout"):
    left, right = st.columns([3, 1.15], gap="medium")

    with left:
        # 页眉与下面的正文区同框，合起来是一页书
        st.markdown(leaf_head_html(game.state.data), unsafe_allow_html=True)
        # 新消息换用新容器，保证即使之前在翻阅历史，发送后也回到最新位置。
        # 普通控件重绘时身份保持稳定，保留用户当前的阅读位置。
        chat_key = f"chat_panel_{len(log)}_{int(busy)}"
        with st.container(height=CHAT_HEIGHT, border=False, key=chat_key, autoscroll=True):
            render_log(show_log)

        # 输入与行动控件独立于历史滚动区，翻阅旧消息时也能直接发送。
        with st.container(key="composer", gap="small"):
            if game.is_over:
                st.info("这段人生已经结束。你可以保存或导出记录，点击侧栏「停止世界」后开始新游戏。")
            if st.session_state.get("turn_error"):
                st.error("本轮未结算，进度保持在行动之前。" + st.session_state.turn_error)
                if st.button("重试这次行动", disabled=busy or game.is_over):
                    if st.session_state.get("failed_block"):
                        st.session_state.pending_block = st.session_state.failed_block
                    else:
                        st.session_state.pending_input = st.session_state.failed_input
                    st.session_state.pop("turn_error", None)
                    st.rerun()

            user_text = st.chat_input("输入你想做的事，或想说的话…", key="chat_input",
                                      disabled=busy or game.is_over)
            if user_text and user_text.strip():
                handle_input(user_text.strip())

            # 被打断的安排：放在展开栏外面，玩家一进来就能看到“继续”。
            # “还有剩余”以 game.pending_block（游戏状态）为准；pending_blk 只是本次待办，
            # 会被立即消费掉，不能用来判断是否还有剩余时间。
            _resume = (game.state.data.get("pending_block") or None) if not busy else None
            if _resume and not game.is_over:
                _pb_plan = _resume.get("plan") or "日常度日"
                _pb_remaining = seasons.clamp_days(_resume.get("remaining"))
                if st.button(f"继续：{_pb_plan}（还剩{seasons.span_text(_pb_remaining)}）",
                             icon=":material/hourglass_top:", key="block_resume", use_container_width=True):
                    handle_block(_pb_plan, _pb_remaining, _resume.get("seclusion") is True, resume=True)

            options = game.state.data.get("options") or []
            if options and not busy and not game.is_over:
                with st.expander(f"可选行动（{len(options)}）", expanded=False):
                    for i, o in enumerate(options):
                        if st.button(o, key=f"opt_{game.state.data['meta']['turn']}_{i}", use_container_width=True):
                            handle_input(o)

            # 岁月模式：安排一段时间由代码按旬推进并结算。
            with st.expander("安排一段时间", expanded=False):
                st.text_input("这段时间做什么", key="block_plan", disabled=busy or game.is_over,
                              placeholder="如：在洞府修炼 / 去坊市摆摊卖丹 / 闭关冲击筑基")
                st.segmented_control("时长", list(seasons.DURATIONS), default="一月", key="block_len",
                                     disabled=busy or game.is_over)
                st.toggle("闭关（少受外界打扰）", key="block_seclusion", disabled=busy or game.is_over)
                if st.button("开始", key="block_start", type="primary", disabled=busy or game.is_over):
                    _plan = (st.session_state.get("block_plan") or "").strip()
                    _label = st.session_state.get("block_len") or "一月"
                    _days = seasons.DURATIONS.get(_label, 30)
                    _secl = bool(st.session_state.get("block_seclusion"))
                    if not _plan:
                        st.warning("请先填写这段时间做什么。")
                    else:
                        handle_block(_plan, _days, _secl)

    with right:
        with st.container(height=CHAT_HEIGHT, border=False, key="world_panel"):
            render_world_panel(game)

# 处理待处理输入（此刻页面已渲染出玩家消息 + 思考占位，随后才阻塞调用模型）
if pending:
    st.session_state.pop("pending_input", None)
    try:
        out = game.run_turn(pending)
    except Exception:
        st.session_state.failed_input = pending
        st.session_state.pop("failed_block", None)
        st.session_state.turn_error = "暂时未能完成回复，请重试。"
    else:
        st.session_state.pop("turn_error", None)
        st.session_state.pop("failed_input", None)
        st.session_state.log.append({"role": "narrator", "content": out})
    st.rerun()

# 处理待结算的时间安排（岁月模式）
if pending_blk:
    st.session_state.pop("pending_block", None)
    try:
        if pending_blk.get("resume"):
            out = game.resume_block()
        else:
            out = game.run_block(pending_blk.get("plan") or "日常度日",
                                 pending_blk.get("days") or 30,
                                 pending_blk.get("seclusion") is True)
    except Exception:
        st.session_state.failed_block = pending_blk
        st.session_state.pop("failed_input", None)
        st.session_state.turn_error = "这段时间的安排尚未结算，请重试。"
    else:
        st.session_state.pop("turn_error", None)
        st.session_state.pop("failed_block", None)
        st.session_state.log.append({"role": "narrator", "content": out})
    st.rerun()
