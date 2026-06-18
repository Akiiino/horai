{
  description = "Horai — a set of routine-keeping personal assistants";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      # Python with the app's runtime deps plus pytest, shared by the checks so
      # imports resolve for both the test run and the type checker.
      checkPython =
        pkgs:
        pkgs.python3.withPackages (ps: [
          ps.python-telegram-bot
          ps.apscheduler
          ps.pytest
        ]);
    in
    {
      packages = forAllSystems (pkgs: {
        eunomia = pkgs.callPackage ./eunomia/package.nix { };
      });

      checks = forAllSystems (
        pkgs:
        let
          py = checkPython pkgs;
        in
        {
          pytest = pkgs.runCommand "eunomia-pytest" { nativeBuildInputs = [ py ]; } ''
            cp -r ${./eunomia} src && chmod -R +w src && cd src
            python -m pytest -q
            touch $out
          '';

          ruff = pkgs.runCommand "eunomia-ruff" { nativeBuildInputs = [ pkgs.ruff ]; } ''
            ruff check --no-cache ${./eunomia}
            touch $out
          '';

          basedpyright =
            pkgs.runCommand "eunomia-basedpyright"
              {
                nativeBuildInputs = [
                  pkgs.basedpyright
                  py
                ];
              }
              ''
                cp -r ${./eunomia} src && chmod -R +w src && cd src
                basedpyright --pythonpath ${py}/bin/python
                touch $out
              '';
        }
      );

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: [
              ps.python-telegram-bot
              ps.apscheduler
              ps.pytest
            ]))
            pkgs.ruff
            pkgs.basedpyright
          ];
        };
      });

      nixosModules.eunomia = import ./eunomia/module.nix self;

      formatter = forAllSystems (pkgs: pkgs.nixfmt-tree);
    };
}
