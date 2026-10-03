# slenpup

PT commitment contract (Base, USDC) and, later, a local video verifier. Design: `SPEC.md`.

## Build and test

Needs Foundry and solc 0.8.37.

```
forge build
forge test
forge coverage --report summary --no-match-coverage "test/"
```

`foundry.toml` pins `solc_version = "0.8.37"`; on a normal machine forge downloads it. In a sandbox
where binaries.soliditylang.org is blocked, point forge at a native-compatible wrapper instead:
`FOUNDRY_SOLC=/path/to/solc forge test`.

The fork test needs `BASE_RPC_URL`.
