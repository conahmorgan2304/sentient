# Sentient development rules

Follow these rules for all Sentient development. See [CLAUDE.md](CLAUDE.md)
for existing project guidance.

- Investigate and fix root causes.
- Check existing functionality before introducing new features.
- Preserve working features and backward compatibility.
- Avoid unnecessary complexity and duplicated functionality.
- Protect household privacy, security and device safety.
- Add regression tests for bug fixes.
- Run syntax validation, relevant tests and linting before committing.
- Never weaken tests simply to make them pass.
- Include an appropriate version bump for releasable changes.
- Review changes for conflicts, performance problems and regressions.
- Claim that tests passed only after verifying the results.
- Report unresolved issues before marking a pull request ready for merging.
- Obtain explicit repository owner approval before merging a pull request or
  publishing a release, including when a merge triggers an automatic release.
