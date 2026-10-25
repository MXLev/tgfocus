# TGFocus privacy information

TGFocus is an unofficial open-source Telegram notification filter. This document describes the application, not the retention practices of Telegram, Microsoft, OpenRouter, a model provider or DeepSeek. The maintainer operates no TGFocus message-processing server and the application has no project telemetry, automatic crash reporting or automatic update checks. Opening a website from the UI is an explicit user action; that website has its own policy.

## Telegram and local filtering

After the user signs in, Telethon connects directly to Telegram using their API ID/hash and session. Telegram supplies account/dialog information and incoming messages required for filtering. Filtering defaults to local rules; turning AI off does not remove the Telegram connection needed to receive messages. The app does not send Telegram messages, mark them read or download attachments. It is not an official Telegram client.

Telegram's policy and API terms: https://telegram.org/privacy and https://core.telegram.org/api/terms . Signing in creates a Telegram device/session that can be revoked in Telegram's Devices settings.

## Optional external AI processing

AI defaults **off**. The user explicitly enables it, supplies their own provider API key, chooses a model/priority threshold and can exclude chats. Excluded chats use local rules. Telegram mute checks, including forum topics, precede AI dispatch; a muted or unverifiable chat is not sent for classification. Some locally accepted messages require no AI request. Disabling AI stops subsequent eligible dispatch; a request already sent cannot be retracted.

For an eligible request, TGFocus sends up to the first **4000 characters** of one message's text/caption, the user's optional priority instruction, the classification prompt and selected model identifier. Names, chat IDs, message history, attachments, Telegram credentials and session strings are not separately added to that request. Personal information can still appear in the message or the user's instruction. The provider API key is sent to the selected provider endpoint to authenticate the request; that provider can also observe network/account metadata such as the source IP.

- **OpenRouter**: https://openrouter.ai/privacy . OpenRouter routes the request to a model provider, which may be selected dynamically, especially with `openrouter/free`. Review the [provider logging information](https://openrouter.ai/docs/guides/privacy/provider-logging) and the applicable end-provider policy before sending confidential information. The [provider catalogue](https://openrouter.ai/providers) and [provider API](https://openrouter.ai/api/v1/providers) identify providers and their policy links. TGFocus requests `data_collection: deny`; this is a routing restriction, **not a guarantee of zero retention**, and is not the same as requesting Zero Data Retention. Provider availability, account logging settings, abuse/legal processing and policies may differ. If no suitable route is available, local fallback applies.
- **Direct DeepSeek**: https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html . Requests go to DeepSeek's API instead of OpenRouter; the selected service's current terms and retention rules apply. The free web chat and API are different services.

The maintainer does not receive these API requests, keys or messages. AI decisions can be wrong. On failure or rate limits, the application falls back to local rules and may show a silent connection-error notification. There is no promise of emergency-message delivery or of external-provider deletion/retention behavior. More usage details: [AI.ru.md](AI.ru.md).

## Stored data

On Windows, `%LOCALAPPDATA%\TGFocus` holds rules/preferences, selected dialog names/IDs, optional AI instructions and provider/model choices. Credentials and sessions are stored separately in `credentials.dpapi`, encrypted with Windows DPAPI for the current user. Codes and two-factor login passwords are not persisted. DPAPI is not protection against software already executing as that same Windows user. On macOS, credentials use Keychain and rules use the app's Application Support directory.

The activity log is bounded, in memory and excludes message bodies. Message content is processed transiently in the local worker and may be displayed in allowed notifications. Windows/macOS can retain notification content in their own notification centres. Disabling previews limits message text displayed, but notification titles may still identify a chat/sender. These OS-managed records are separate from TGFocus's files.

The Windows app creates its own current-user Start Menu shortcut and notification registration. It adds a Startup-folder shortcut only following an explicit checkbox/confirmation. Windows retains control over whether startup and notifications are allowed. Reading DND through WNF changes no system profile. No administrator privilege, service, scheduled task, OS protection exclusion or background self-update is installed.

## Deletion and user controls

Turn AI off or add exclusions before further sensitive messages; review/revoke API keys in the provider's account when needed. To revoke Telegram access, use TGFocus logout or remove its device in Telegram. Deleting an app folder or local session file alone does not revoke a server-side session or an API key.

Before removing the Windows program, remove its startup entry, exit it and delete the portable folder. Choose separately whether to retain `%LOCALAPPDATA%\TGFocus` for a reinstall or delete its local rules and encrypted credentials. Remove only TGFocus's own Start Menu shortcut and verified notification identity; leave other applications and OS-managed notification/startup settings alone. [Detailed removal instructions](WINDOWS-UNINSTALL.ru.md) identify the exact paths and checks. Clear TGFocus notifications through the OS notification centre if desired; this does not delete copies already held by Telegram or an AI provider.

Do not post private messages, API hashes/keys, session files or passwords in GitHub issues or diagnostic uploads. Information voluntarily posted to GitHub is processed under [GitHub's privacy statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement). Security reporting is described in [SECURITY.md](SECURITY.md). This document will be updated when TGFocus data flows change.
