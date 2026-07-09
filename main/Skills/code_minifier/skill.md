---
name: solidity-minifier-rules
description: Use this skill to compress raw Solidity source code retrieved via the Etherscan MCP server.
---
# Solidity Minification Requirements

To maintain extreme token efficiency during security scans, apply this regex-driven cleaning process to code text before routing it to the LLM context window:

1. Locate and strip all single-line comments (`// ...`) and block multi-line comment footprints (`/* ... */`).
2. Remove standard compiler directives (`pragma solidity ...`) and structural interface descriptors.
3. Consolidate consecutive empty lines, tab spaces, and line breaks into a single space token.
4. Verify the compressed payload has shrunk by at least 60% from the raw input size.