# Security Policy

## Reporting a vulnerability

Please report security issues privately through GitHub's vulnerability-reporting / Security Advisory flow when available. If that is not available, open a minimal public issue asking for a private contact path without including exploit details or sensitive data.

## Supported versions

The latest release is the primary supported version. Older releases may not receive security fixes.

## Security scope

Security-relevant areas include archive extraction, path handling, decompression/resource limits, subprocess invocation of the bundled converter, temporary/output-file handling, and cases where a malformed map can cause the tool to write outside its intended working/output paths or execute unintended code.

A map that merely fails to convert, remains too large, or still fails in FS25 is normally a correctness/compatibility bug rather than a security vulnerability unless it crosses one of those trust boundaries.

## Trust boundaries

- Input map archives are treated as untrusted.
- The original input archive should never be modified in place.
- `grleconvert` is a bundled third-party native executable and therefore part of the trusted computing base for compiled density-layer conversion.
- Output from a successful run is not a claim that the map is safe, valid, or compatible with every FS25 build; it only means the tool completed its documented checks and transformations.

Please avoid attaching private maps, credentials, server data, or other sensitive material to public reports unless it has been deliberately redacted and you have permission to publish it.
