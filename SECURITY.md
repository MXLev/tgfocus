# Security reports

Do not post Telegram API hashes, session strings, login codes, passwords, private messages, or exploit details in public issues.

If the repository's **Security → Report a vulnerability** option is available, use it for a private report. Otherwise, open an issue requesting a private contact channel without including vulnerability details; wait for the maintainer to provide one before sharing sensitive information.

Include the affected version, operating system, reproduction steps with synthetic data, and the impact. Reports and fixes are handled on a best-effort basis; there is no guaranteed response time. Please use the latest release when checking whether a problem still occurs.

Release DMGs use an ad-hoc signature and are not notarized by Apple. Download only from this repository's releases. Published SHA-256 checksums help detect damaged or mismatched downloads; they are not independent proof of publisher identity.

Optional AI classification sends up to 4000 characters of eligible messages to the selected external provider only after opt-in. Telegram mute settings and AI chat exclusions are checked before dispatch. API keys are stored in Keychain and passed through stdin IPC, never saved in rules or argv. Fixed HTTPS endpoints, redirect rejection, bundled CA verification, bounded concurrency/rate/response size, redacted errors and local fallback limit exposure and resource use. Messages themselves may contain personal data; an already dispatched request cannot be retracted. See [AI.ru.md](AI.ru.md).

Optional Shortcuts automation runs a user-selected shortcut as that user. Review shortcut actions before enabling it. Only explicit ON/OFF results enable delivery; missing/invalid state pauses it. See [AUTOMATION.ru.md](AUTOMATION.ru.md).

The experimental Windows client encrypts credentials with current-user Windows DPAPI and keeps them out of the settings JSON. DPAPI does not protect against another program already running as that same user. Its bounded IPC queue applies configuration changes independently of slow Telegram account commands; logging out only deletes local credentials after Telegram confirms revocation. Optional DND automation reads an undocumented WNF state and pauses on unknown status; it never writes system profiles and has no Focus Sessions fallback. Native notifications still follow Windows permissions and priority settings. See [WINDOWS.ru.md](WINDOWS.ru.md).
