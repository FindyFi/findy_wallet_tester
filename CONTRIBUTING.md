# Contributing

Thank you for your interest in contributing! Contributions are welcome in the following areas:

- **New wallet implementations** — support for additional digital credential wallets
- **Bug fixes** — fixes to existing wallet flows, base framework, or providers
- **New providers** — additional credential offer sources beyond the existing web, config, and ITB providers
- **Infrastructure improvements** — reporting, test orchestration, device support

---

## Reporting Issues

Please [open a GitHub issue](https://github.com/FindyFi/findy_wallet_tester/issues) and include:

- **Type:** bug or feature request
- **OS and Android emulator/device** used
- **Appium version** (`appium --version`)
- **Python version** (`python --version`)
- **Steps to reproduce** and the **expected vs. actual behaviour**
- **Relevant log output** from `reports/<timestamp>/<wallet>/test.log` or `app.log`

---

## Development Setup

Follow the [README setup instructions](README.md#setup) to get the full environment running.

For wallet development, start Appium with the Inspector plugin enabled so you can inspect screen elements:

```bash
appium --allow-cors --use-plugins=inspector
```

Then open the Appium Inspector UI in your browser to find element locators for the wallet screens you are implementing.

---

## Adding a New Wallet

Adding a wallet is the most common type of contribution. The `wallets/example/` directory is a template — copy it and fill in the TODOs.

**Step-by-step:**

1. **Copy the example wallet:**
   ```bash
   cp -r wallets/example wallets/<wallet_name>
   ```

2. **Update `wallets/<wallet_name>/config.json`:**
   - Set `application.package` and `application.activity` (find these with `adb shell pm list packages` and Appium Inspector)
   - Set `application.pin` if the wallet uses a PIN
   - Add your `test_cases` (issuance and/or verification endpoints)

3. **Fill in locators in `wallets/<wallet_name>/pages/`:**
   Use Appium Inspector or `adb` to find element IDs, accessibility labels, or XPath expressions for each screen. Replace the `TODO` constants in each page class.

4. **Implement flows in `wallets/<wallet_name>/flows/`:**
   Follow the inline `TODO` comments in each flow file. Flows call page methods to navigate through onboarding, credential acceptance, and verification.

5. **Run your wallet locally:**
   ```bash
   python runners/run_tests.py <wallet_name>
   ```
   Check the generated report under `reports/<timestamp>/<wallet_name>/`.

6. **Include the HTML report in your PR** (or attach it as an artifact) so reviewers can see a successful run.

---

## Code Style and Conventions

**Python:** Follow [PEP 8](https://peps.python.org/pep-0008/).

**Naming:**
| Thing | Convention |
|---|---|
| Classes | `PascalCase` |
| Functions and variables | `snake_case` |
| Private functions | `_leading_underscore` |
| Locator constants | `UPPER_CASE` |
| Config JSON keys | `snake_case` |

**Logging:**
```python
import logging
logger = logging.getLogger(__name__)

logger.info("[module_name] Descriptive message")
logger.debug("[module_name] Detailed trace message")
```

Use bracket-tagged prefixes (`[install]`, `[credential_flow]`, `[web_provider]`, etc.) to make log lines easy to filter.

**Configuration:**
- All test data must come from `config.json` — no hardcoded values in test or flow files.
- Never commit credentials. Use `${VAR_NAME}` placeholders in config and load real values from `.env` or environment variables.
- Timeouts should be read from config using the hierarchical lookup (`timeouts.<key>` → `timeouts.default` → hardcoded fallback).

---

## Pull Request Process

1. **Fork** the repository and create a feature branch from `main`.
2. **Keep PRs focused** — one wallet or one feature per PR makes review easier.
3. **Test locally** before opening the PR. Ensure `python runners/run_tests.py <wallet_name>` completes without unexpected failures.
4. **Describe what you tested** in the PR description: which wallet, which credential flows, which emulator/device, and the Appium + Android versions used.
5. **Attach or link a test report** (the HTML report from `reports/`) for wallet contributions. Infra-only changes do not require a report.

---

## Project Structure

See the [Project structure section in the README](README.md#project-structure) for an overview of directories and their responsibilities.

---

## License

By submitting a contribution you agree that your changes will be licensed under the same license as the rest of the project.
