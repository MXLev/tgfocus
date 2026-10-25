# Signing access checklist for MXLev

The public roles/process are in [CODE_SIGNING_POLICY.md](../CODE_SIGNING_POLICY.md). These are recorded statuses, not account settings applied by this file.

- [x] MXLev confirmed the single-maintainer author/reviewer/approver roles on 2026-10-25. Codex/CI are not an independent human reviewer or signing approver.
- [x] MXLev confirmed GitHub MFA is enabled on 2026-10-25. The session's GitHub API returned no readable MFA field, so this is the owner's confirmation, not an API audit.
- [ ] Create/finish the SignPath account/organization and enable MFA there. Every person later granted access must enable MFA on both platforms before signing access is granted.
- [ ] Apply to [SignPath Foundation](https://signpath.org/apply), linking the public Windows release, tagged source, code signing policy, privacy and removal instructions. The owner supplies real contact/identity details directly to the service.
- [ ] Receive actual approval and configure only the agreed product/version/file policy and CI integration. No sponsorship, certificate or integration currently exists.
- [ ] Confirm manual approval for **every** signing request. An automated build/test result must not auto-approve it.

No password, MFA seed/recovery code, identity document, certificate private key or provider API key should be posted in the repository or chat. Store only an approved integration credential through the service/GitHub secret interface if and when one is required. A personal GitHub repository does not itself prove that every collaborator has MFA; verify the access requirement before adding a person.

The first unsigned Windows release can be published before SignPath onboarding. Signing must remain disabled until the unchecked access/approval items are complete. Do not replace the unsigned release asset with a different signed file under the same name/version; publish a new release with new hashes when signing becomes available.
