# Security policy

## Supported versions

The project is pre-1.0. Only the latest release and the `main` branch receive security fixes.

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Report vulnerabilities privately through GitHub's [private vulnerability reporting](https://github.com/SaltedTan/virgil-language-server/security/advisories/new). Include:

- a description of the problem and its impact;
- the affected version or commit;
- steps or a minimal file/workspace that reproduces it.

You should receive an acknowledgement within 7 days. This is a volunteer-maintained project, so fix timelines depend on severity and maintainer availability. Once a fix is available, the maintainer will publish an advisory and credit you unless you prefer otherwise.

## Scope

Issues in scope include, for example:

- the server executing code from a workspace (user initializers, build scripts, downloaded binaries) when it should only parse and type-check;
- crashes or unbounded resource use triggered by crafted protocol messages or source files;
- path traversal or file access outside the configured workspace through project configuration;
- tampered or unverifiable release artifacts.

Vulnerabilities in the Virgil compiler itself should also be reported upstream to [titzer/virgil](https://github.com/titzer/virgil).
