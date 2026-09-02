# Toppan has no landing screen, but it does have onboarding: a fresh install opens on a
# "Select Language" modal. See pages/language_page.py.
#
# This file previously stated that toppan "opens directly to the home screen", which init_flow
# relied on. That was never tested — nothing had ever wiped the wallet — and the first run that
# did (2026-09-02) disproved it, erroring all 11 toppan cases in setup.
#
# Kept as a placeholder so the wallet's page set matches the others.
