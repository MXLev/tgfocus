# GitHub settings for the maintainer

These are recommended settings, not settings applied by the files in this repository. The onboarding session could read Git refs but received `Forbidden` from the GitHub API, so hosted settings could not be inspected or changed.

- **General:** use Public visibility if public distribution is intended. Before changing a private repository to public, review its full history and files for credentials and personal data. The MIT license is already included.
- **About:** description: `Local Telegram notification filter for macOS, built with SwiftUI and Telethon.` Suggested topics: `macos`, `swiftui`, `telegram`, `telethon`, `notifications`, `python`.
- **Features:** enable Issues. Enable Discussions only if the maintainer wants a community support forum.
- **Pull Requests:** enable squash merging and automatic deletion of merged branches.
- **Actions → General:** enable GitHub Actions; default workflow token permissions should be read-only. The release job explicitly requests write access. Require approval for workflows from first-time outside contributors. Do not provide release credentials to pull-request jobs.
- **Security:** enable private vulnerability reporting, Dependabot alerts, and available secret scanning/push protection features. Dependency-update configuration is included in `.github/dependabot.yml`.
- **Rules → Rulesets:** import [rulesets/main.json](rulesets/main.json) using the steps below.

## Import the main branch ruleset

1. Push the CI workflow on a development branch and open a pull request. Let the `Python tests` and `macOS app and DMG` checks run successfully before enabling the ruleset. Fix a failing build first, and confirm the displayed check names match the JSON.
2. Download `.github/rulesets/main.json` from that branch, or use your local copy.
3. Open **Settings → Rules → Rulesets → New ruleset → Import a ruleset** in GitHub and select the JSON file.
4. Review `Protect main`, confirm that the enforcement status is **Active**, and save it. If a ruleset already protects `main`, review and reconcile it instead of blindly adding a duplicate; multiple active rulesets apply together.

The ruleset applies only to `main`. It requires a pull request, both CI checks, an up-to-date branch before merging, and resolved review conversations. It blocks branch deletion and force pushes. There are no bypass actors. It does not restrict development branches or release tags.

The required approval count is **0**, so a solo maintainer can merge their own pull request after CI passes. If another maintainer joins, consider increasing it to **1**. Changes pushed to a reviewed PR dismiss stale approvals.

The file is an importable configuration, not an automatically applied GitHub setting. It can also be submitted as the JSON body to `POST /repos/MXLev/tgfocus/rulesets` by a maintainer with suitable API access. Do not run that create operation repeatedly: inspect existing rulesets first and update the matching rule instead. Hosted ruleset support depends on repository visibility and GitHub plan.

Do not publish the first DMG until a macOS CI build and manual installation/first-launch test have succeeded. Tag pushes create draft releases for maintainer review.
