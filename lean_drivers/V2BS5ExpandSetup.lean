/-
Copyright (c) 2026.
Released under Apache 2.0 license.

Helper for producing the exact transitive Lake `ModuleSetup` used by the
oracle-safe S5 verifier.  Unlike `lake query +Module:setup` on Lake 4.32,
`Lake.setupServerModule` expands transitive import artifacts.  `noBuild := true`
makes a missing dependency a hard failure rather than rebuilding it.  The
production wrapper must still mount the frozen workspace read-only because
Lake may otherwise write no-build trace state while loading it.
-/

import Lake
import Lake.Load.Workspace

open Lean System Lake

namespace V2BS5ExpandSetup

private def fail (message : String) : IO UInt32 := do
  IO.eprintln s!"V2B-S5-SETUP-ERROR: {message}"
  return 1

private def failIO {α : Type} (message : String) : IO α :=
  throw (IO.userError message)

def main (args : List String) : IO UInt32 := do
  let [workspaceRoot, sourceFile, toolchainRoot] := args
    | fail "usage: V2BS5ExpandSetup <workspace-root> <absolute-source-file> <toolchain-root>"

  -- Resolve through Lake's public detector, then require exact equality to the
  -- producer-bound toolchain root before using the result.
  let (elanInstall?, leanInstall?, lakeInstall?) ← findInstall?
  let some leanInstall := leanInstall?
    | failIO "could not resolve the active Lean installation"
  let expectedToolchainRoot ← IO.FS.realPath toolchainRoot
  let detectedToolchainRoot ← IO.FS.realPath leanInstall.sysroot
  unless detectedToolchainRoot == expectedToolchainRoot do
    failIO s!"toolchain mismatch: {detectedToolchainRoot} != {expectedToolchainRoot}"
  let lakeInstall := lakeInstall?.getD (LakeInstall.ofLean leanInstall)
  let lakeEnv ← EIO.toIO (fun message => IO.userError message) <|
    Lake.Env.compute lakeInstall leanInstall elanInstall? (noCache := some true)

  let wsDir ← IO.FS.realPath workspaceRoot
  let path ← IO.FS.realPath sourceFile
  let loadConfig : LoadConfig := {
    lakeEnv
    wsDir
    reconfigure := false
    updateDeps := false
    updateToolchain := false
  }
  let some workspace ← (loadWorkspace loadConfig).toBaseIO
    | failIO "failed to load the frozen Lake workspace"
  let some module := workspace.findModuleBySrc? path
    | failIO s!"source is not one exact module in this workspace: {path}"
  let buildConfig : BuildConfig := {
    noBuild := true
    trustHash := false
    out := .stderr
  }
  let setup ← workspace.runBuild (cfg := buildConfig) do
    setupServerModule path.toString path none
  unless setup.name == module.name do
    failIO s!"setup module mismatch: {setup.name} != {module.name}"
  IO.println (Lean.toJson setup).compress
  return 0

end V2BS5ExpandSetup

def main (args : List String) : IO UInt32 :=
  V2BS5ExpandSetup.main args
