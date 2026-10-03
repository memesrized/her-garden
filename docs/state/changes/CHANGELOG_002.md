# Changes

## 2026-10-03 — Telegram watering-plan overview

- Change: Add `/watering_plan` to the bot's command menu and start screen. It lists only active
  plants with enabled schedules, showing cadence, start date and next local reminder. Lists above
  ten plants use location buttons and ten-plan pages, including a group without a location.
- Reasoning: A dedicated read-only view makes current plans easy to check without opening each
  plant. Existing editor pagination, MCP tools, schedule storage, and delivery logic stay intact.
- Verification: Bot tests cover short, empty and paged views, filtering, timezone formatting and
  navigation. The executed public-fixture notebook shows both short and long lists.
- Files: `src/her_garden/bot.py`, `tests/test_bot.py`,
  `notebooks/demos/telegram_navigation.ipynb`, `README.md`, `docs/STATE.md`,
  `docs/state/system/data_flow.md`, `docs/state/system/operations.md`,
  `docs/state/architecture/decisions.md`, `docs/state/changes/README.md`,
  `docs/state/changes/CHANGELOG_002.md`.

## 2026-10-03 — Reminder for ChatGPT tool discovery

- Change: Add a repository-local agent instruction to remind the owner to refresh or reconnect
  their ChatGPT MCP connection after deploying new tool metadata, and where to find the private
  household credential if OAuth login is required.
- Impact: Future tool additions include an explicit client-discovery handoff without storing
  credentials in the repository.
- Files: `AGENTS.md`, `docs/state/changes/CHANGELOG_002.md`.
