{ python3Packages, pyproject-nix }:
let
  project = pyproject-nix.lib.project.loadPyproject { projectRoot = ./.; };
in
python3Packages.buildPythonApplication (
  project.renderers.buildPythonPackage { python = python3Packages.python; }
  // {
    nativeCheckInputs = [ python3Packages.pytestCheckHook ];
  }
)
