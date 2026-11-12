# Wanjie 万界 · AI Text Adventure

**Create a world, play your character, and choose what happens next. Let an AI game master tell the story.**

[简体中文](README.md) · [English](README_EN.md)

Wanjie is a **DeepSeek-powered text adventure** that runs in your local browser. Build a setting, meet characters, explore, and take risks. World state, character histories, and promises are saved between sessions to help the story continue.

**The game currently uses a Chinese interface and primarily Chinese prompts.** This page provides an English overview and setup guide; it does not add an English game mode.

## What can you do?

- **Build your own world.** Generate a setting with AI, import a JSON / TXT / Markdown file, or enter it yourself. Choose your character's talents, physique, and special abilities.
- **Choose your actions in words.** Type freely or use the suggested actions. Explore, talk, persuade, or fight to move the story forward.
- **Keep a history with important characters.** Character profiles store personality, speech style, goals, and mood alongside shared experiences. Promises can be kept or broken.
- **Pick a prose style.** Plain, standard, and literary modes are available. Plain is the default, while character profiles guide individual voices.
- **Spend time in the world.** Schedule training or everyday activities. The time-passage system advances the calendar and handles interruptions and incidental events.
- **Keep multiple adventures.** The web interface supports separate saves and JSON save import/export.

**The AI narrates; Python resolves the rolls.** Code determines combat outcomes, action checks, damage, and death. The model receives those results and continues the story.

## Quick start

You need **Python 3.10+**, an internet connection, and your own **DeepSeek API key**. Model calls are billed by DeepSeek. The **Streamlit web interface** has the most complete feature set.

### 1. Get the project

Choose **Code → Download ZIP** on GitHub and extract the archive, or clone it:

```bash
git clone https://github.com/yiboyang57-commits/ai-dialogue-game.git
cd ai-dialogue-game
```

### 2. Start the game

After installing Python, you can double-click the bundled launcher:

- **macOS:** `启动游戏.command`
- **Windows:** `启动游戏.bat`

On first launch, the launcher creates a local environment, installs dependencies, and opens your browser. To start manually:

**macOS / Linux**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run app.py
```

**Windows (PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

### 3. Enter your key and create a world

Enter your DeepSeek API key when prompted, then choose a setting and build your character. The key is stored locally in `config.json`; the `DEEPSEEK_API_KEY` environment variable is also supported.

Saves are stored locally in `save/`. Story context is sent to DeepSeek during play. Both `config.json` and `save/` are excluded from Git; do not include your key in issue reports or shared project files.

## How memory works

The game maintains structured JSON state for the world, player, characters, inventory, and ongoing events. The model receives a state summary, chapter/global summaries, and the latest three turns rather than the entire conversation.

An optional SQLite layer records turns, state changes, entities, and facts, and retrieves relevant past events. It is disabled by default. To enable it on macOS / Linux:

```bash
WANJIE_STORAGE=sqlite .venv/bin/python -m streamlit run app.py
```

On Windows (PowerShell):

```powershell
$env:WANJIE_STORAGE = "sqlite"
.\.venv\Scripts\python.exe -m streamlit run app.py
```

JSON remains the primary save format. SQLite retrieval uses keyword and entity matching; semantic search is not implemented. Character profiles and promises also work with SQLite disabled.

## Project status

Current version: **v2.25**. This is an actively developed project, and story consistency and prose quality still need work. Character profiles and prose controls are implemented, but their effect on real model output has not yet been evaluated with the prepared paid-model tests. Memory can still miss details.

The automated suite contains **178 tests** and uses fake model responses rather than paid API calls. From the project root:

```bash
.venv/bin/python -m unittest tests.test_gameplay tests.test_character_pool tests.test_character_pool_ui tests.test_chat_ui
```

On Windows, replace `.venv/bin/python` with `.\.venv\Scripts\python.exe`.

The core uses the Python standard library; Streamlit is the web interface's additional dependency. Older command-line (`main.py`) and tkinter (`gui.py`) interfaces are also included, but do not have all the web interface's features.

## Feedback

Tried a world? Found an awkward line, a forgotten event, or a confusing interaction? Share it in [Issues](https://github.com/yiboyang57-commits/ai-dialogue-game/issues). Include the version, your input, and the relevant reply excerpt so the problem is easier to reproduce.

Further documentation is currently in Chinese:

- [Detailed features and architecture](README.md#架构设计)
- [Changelog](更新记录.md)
- [Development roadmap](完整项目路线图.md)
- [Task status](任务书状态.md)
- [Memory evaluation](docs/memory_eval.md)
- [Prose evaluation](docs/style_eval.md) and [character voice evaluation](docs/voice_eval.md)
