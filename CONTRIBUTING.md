# Contributing to FS25_16xMapFix

Issues and pull requests are welcome, especially when they include a reproducible map case or a small fixture that demonstrates the behavior being changed.

## Ground rules

- Keep changes focused and explain the failure mode or use case they address.
- Add or update tests for behavior changes where practical.
- Preserve the tool's safety properties: never modify the input archive in place, keep archive/path guards intact, and surface uncertain map cases instead of silently guessing.
- Do not commit private maps, credentials, server data, or other material you do not have permission to publish.
- CI should pass before a PR is merged.

## Evidence

Claims about FS25 internals should be scoped to the game build, source snapshot, or field observation they came from. A reproduced mitigation is useful evidence; it is not automatically proof of a proprietary engine root cause.

## Commit identity

The maintainer uses a GitHub noreply address for personal OPSEC. Contributors may use the Git identity and privacy settings appropriate for them; no particular email format is required by this project.

## License

By contributing, you agree that your contribution is licensed under the repository's applicable license terms. See [LICENSE](LICENSE) and any file-specific notices for details.
