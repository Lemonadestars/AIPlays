# AIPlays — let AI models play Baldur's Gate 3

[中文说明](README.md)

Claude, GPT, Gemini, DeepSeek, Grok, Qwen and friends each take over a party member: they fight, talk to NPCs,
roll their own dice and banter with each other, with voices and bilingual subtitles for OBS. You can watch, or
play one of the characters yourself as the party captain. Built for streams and videos.

- **Combat**: the AI sees the battlefield (positions, HP, spell ranges, the game's combat log) and acts on its own.
- **Dialogue**: AIs stay quiet while NPCs talk; at a choice, teammates argue (the "party council"), then the speaking character decides.
- **Dice**: AIs roll their own skill checks, celebrate nat 20s, mock nat 1s, and decide whether to spend Inspiration on a reroll.
- **Long-term memory** of plot reveals, big decisions and running jokes.
- **Producer hints**: when an AI gets stuck, type a hint in a small window; pause all AIs with one checkbox.

> Unofficial fan project, not affiliated with Larian Studios. The persona prompts and voices are tuned for
> Chinese-language streams (subtitles are bilingual).

## Install

You need Windows 10/11, Baldur's Gate 3 (Steam or GOG) and at least one AI API key (DeepSeek is the cheapest).

**One line** — open PowerShell and paste:

```powershell
irm https://raw.githubusercontent.com/Lemonadestars/AIPlays/main/install.ps1 | iex
```

**Or** download the ZIP, extract it and double-click `AIPlays.bat`.

The menu then walks you through everything: **1** installs Python, dependencies, Script Extender and the mod;
**2** stores your API keys locally in `director/keys.env`; **3** chooses who plays which character; **4** tests
the connections; **5** starts the director and the hint window.

OBS: add a Browser source with `http://localhost:8765/overlay.html` (1920×1080) and tick "Control audio via OBS".

## Credits

[Norbyte's BG3 Script Extender](https://github.com/Norbyte/bg3se) ·
[Move With Minimap](https://github.com/tldev/bg3-move-with-minimap-mod) (pathfinding check) ·
[VoteAnswers](https://github.com/HateTM/VoteAnswers) (reading dialogue options)

## License

[MIT](LICENSE) © 2026 Artimiss
