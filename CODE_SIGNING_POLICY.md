# TGFocus code signing policy

## Current status

TGFocus is an MIT-licensed open-source Telegram notification filter maintained at https://github.com/MXLev/tgfocus. Windows releases are currently **unsigned**. SignPath Foundation sponsorship, a certificate and a signing integration have not been approved or provisioned. This policy describes the controls required before signing is enabled; it is not a claim of endorsement by SignPath or Microsoft.

## People and responsibilities

| Role | Current responsible party |
| --- | --- |
| Maintainer and first-party code author | [MXLev](https://github.com/MXLev), assisted by OpenAI Codex; contribution authorship is recorded in Git history |
| Release reviewer | MXLev reviews the changes, CI results, dependencies, permissions, privacy behavior and manual checks |
| Signing and publication approver | MXLev personally approves each signing request and release |

This is a single-maintainer project. Review and approval are performed by the same person; there is no claim of independent human review or separation of duties. Codex and CI provide implementation assistance and automated checks, not human approval. Additional maintainers or signing approvers must be named here before receiving access.

## Access and MFA

All people with repository write/release privileges or SignPath access must enable MFA on both GitHub and SignPath before participating in signing. On 2026-10-25, MXLev confirmed that GitHub MFA is enabled. SignPath account/MFA onboarding is still pending. Signing remains disabled until that onboarding and the signing provider's approval are complete.

Each person uses an individual account. Passwords, MFA recovery codes and certificate private keys must not be stored in the repository, build logs, issues or chat. The signing private key is intended to remain with the approved signing service; this project does not distribute a private key or ask users to install a self-signed trust root. GitHub workflow permissions remain read-only except for a narrowly scoped release-draft job. Pull-request jobs receive no signing credentials.

## Review and release process

1. Review the exact source commit and pull-request diff, including dependencies, bundled notices, startup behavior, native APIs and any change in outbound data. Resolve relevant findings. AI-generated changes receive the same review as other contributions.
2. Build on Windows CI from the published commit. Both first-party EXEs must report ProductName `TGFocus` and the same ProductVersion as the Windows release. Run the shared/Windows tests, frozen worker and GUI smoke checks, native integration checks and Microsoft Defender with updated definitions. Failed or unavailable scans do not count as clean results. CI does not establish a Google Safe Browsing verdict or replace desktop testing.
3. The maintainer reviews the exact artifact and its SHA-256, source commit and CI run. Before describing a version as stable, manually check installation/first launch, Telegram connection and notifications, mute handling, optional AI/exclusions, DND and any changed startup behavior. Record the checks and their scope in the release notes; do not invent checks that were not performed. Preliminary releases remain clearly labelled.
4. **Every signing request requires MXLev's manual approval.** Tests passing, an agent instruction or a successful workflow must never automatically approve signing. Check MFA/access, the intended files and product versions, the source-to-artifact link and relevant manual results before approval. The signing provider may impose additional controls.
5. If signing is approved and configured, submit only the agreed first-party `TGFocus.exe` and `helper/focus-worker.exe` from that verified build. Do not sign arbitrary user uploads or re-sign third-party Qt/Python/dependency files as TGFocus without an explicitly approved policy. Validate Authenticode against the agreed signer and timestamp policy after signing, rerun applicable package checks, and calculate checksums for the **final** files/archive.
6. Review the release draft and publish it explicitly. Each published Windows release has a numbered tag, linked source and CI run, download asset, SHA-256 and a clear signing status. Do not silently replace an existing release asset or retarget its tag; publish a new version for changed binaries. A signing sponsor is credited only after an actual grant and integration.

The signing provider must approve the eventual service configuration and allowed file set. No SignPath approval, timestamp authority, certificate identity or automatic signing job is configured by this document.

## Privacy, installation and removal

[PRIVACY.md](PRIVACY.md) describes Telegram connectivity, local storage and optional OpenRouter/end-provider or DeepSeek processing, including links to their policies. External AI processing defaults off and requires the user's opt-in; confidential-chat exclusions apply before dispatch. Neither code signing nor this policy promises that an external provider stores no data.

The portable Windows app creates its own current-user Start Menu shortcut and notification identity. Startup registration is a separate explicit opt-in. It uses no administrator privilege, service, scheduled task or automatic updater. See [Windows setup](WINDOWS.ru.md) and [system changes/removal](WINDOWS-UNINSTALL.ru.md). Do not delete another application's shortcuts, registry values, notification settings or data during cleanup. Licences, notices and corresponding project source accompany the package.

## Incidents

Report vulnerabilities as described in [SECURITY.md](SECURITY.md), without posting secrets or private messages. If repository/signing access or a release is suspected compromised, stop publication/signing, revoke affected access, notify the signing provider through its official support process and investigate source/build provenance before resuming. Corrected binaries receive a new version. Disclose relevant known problems on the affected release page; keep hashes and investigation evidence available without publishing user secrets.
