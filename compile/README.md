# compile/ — frida-compile workspace

This directory hosts the **frida-compile** toolchain (npm) used to bundle the
`*.entry.js` frida scripts in the parent directory into runnable
`*.bundle.js` files. It is the only node-side dependency in the project.

## Layout

| Path | Role |
|------|------|
| `package.json` / `package-lock.json` | pinned deps: `frida-compile`, `frida-java-bridge` |
| `node_modules/` | local install; **gitignored** (see root `.gitignore`) |
| `*.entry.js` | frida sources that need `frida-java-bridge`; each gets bundled into `../<name>.bundle.js` |

The entry scripts themselves live in **`compile/`** (not the project root)
because `package.json`'s relative `scripts.build` paths assume they are here.
The corresponding `*.bundle.js` outputs are dropped at the project root,
where `frida -H ... -l <name>.bundle.js` finds them — and where `*.bundle.js`
is gitignored as a build artifact.

## Why frida-compile at all

frida 17 removed the `Java` / `ObjC` globals, so any hook using the Java
bridge must `import Java from 'frida-java-bridge'` and assign it to
`globalThis.Java`. Bundling bundles those imports into a single self-contained
`*.bundle.js` so the host can run `frida -l <name>.bundle.js` without an
external module resolver.

## MGOP-oracle variants (`mgop_oracle*.entry.js`)

These are **iterations of the same idea** — a spawn-time antidetect (msaoaidsec
watchdog neuter) plus an MGOP `sign` / `aosSign` oracle for the **警察叔叔
jwztc** backend (the `WFJB` side; not the Amap AOS side). They are kept as
reference for the offline mgop-sign reverse work tracked in
`police_report/docs/superpowers/specs/2026-07-12-offline-mgop-sign-blackbox-design.md`.

| File | Status |
|------|--------|
| `mgop_oracle.entry.js` | **v1** — first pass; full antidetect + broad hooks |
| `mgop_oracle2.entry.js` | tightening (less verbose, focused on the `sign` path) |
| `mgop_oracle3.entry.js` | adds asac / asad capture alongside `sign` |
| `mgop_oracle4.entry.js` | offline-friendly: dumps the inputs to `sign` instead of RPC |
| `mgop_oracle5.entry.js` | matches `mgop_oracle4` but split per-call JSONL records |
| `mgop_oracle6.entry.js` | **latest** — minimal hook surface, JSONL streaming, used by the current offline reverse pass |
| `dump_gw.entry.js` | ad-hoc helper: enumerates gateway / dispatcher classes (one-shot recon) |

**Pick `mgop_oracle6.entry.js`** for new work; older files are kept only as
historical reference (don't delete — they're pinned to specific experiment
hypotheses). When a variant's hypothesis is verified, its role moves into
`police_report/docs/superpowers/specs/<date>-<topic>-design.md`.

## Building a bundle

```bash
cd compile
npm install                                  # one-time, creates node_modules/
npx frida-compile ../oracle.entry.js -o ../oracle.bundle.js
npx frida-compile mgop_oracle6.entry.js -o ../mgop_oracle6.bundle.js
```

The `package.json` `scripts.build` entry only handles `../recon.entry.js`
(the Amap side). Add an explicit `npx frida-compile ...` invocation for any
other `.entry.js` you want bundled.
