{
  description = "Horai — a set of routine-keeping personal assistants";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    pyproject-nix.url = "github:pyproject-nix/pyproject.nix";
    pyproject-nix.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs =
    {
      self,
      nixpkgs,
      pyproject-nix,
    }:
    let
      inherit (nixpkgs) lib;

      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      # Every Horai assistant lives in its own subdirectory with a pyproject.toml.
      # Add new assistants here; packages, checks and the devShell pick them up.
      projectDirs = {
        eunomia = ./eunomia;
      };

      # Dependency metadata read once from each assistant's pyproject.toml, so the
      # runtime/dev dependency lists never have to be repeated in Nix.
      projects = lib.mapAttrs (
        _name: dir: pyproject-nix.lib.project.loadPyproject { projectRoot = dir; }
      ) projectDirs;

      # A Python env with an assistant's runtime deps plus its `dev` group (pytest),
      # shared by the checks so imports resolve for both pytest and the type checker.
      checkEnv =
        pkgs: project:
        pkgs.python3.withPackages (
          project.renderers.withPackages {
            python = pkgs.python3;
            groups = [ "dev" ];
          }
        );
    in
    {
      packages = forAllSystems (
        pkgs:
        lib.mapAttrs (
          _name: dir: pkgs.callPackage (dir + "/package.nix") { inherit pyproject-nix; }
        ) projectDirs
      );

      checks = forAllSystems (
        pkgs:
        lib.concatMapAttrs (
          name: project:
          let
            dir = projectDirs.${name};
            py = checkEnv pkgs project;
          in
          {
            "${name}-pytest" = pkgs.runCommand "${name}-pytest" { nativeBuildInputs = [ py ]; } ''
              cp -r ${dir} src && chmod -R +w src && cd src
              python -m pytest -q
              touch $out
            '';

            "${name}-ruff" = pkgs.runCommand "${name}-ruff" { nativeBuildInputs = [ pkgs.ruff ]; } ''
              ruff check --no-cache ${dir}
              touch $out
            '';

            "${name}-basedpyright" =
              pkgs.runCommand "${name}-basedpyright"
                {
                  nativeBuildInputs = [
                    pkgs.basedpyright
                    py
                  ];
                }
                ''
                  cp -r ${dir} src && chmod -R +w src && cd src
                  basedpyright --pythonpath ${py}/bin/python
                  touch $out
                '';
          }
        ) projects
      );

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (
              ps:
              lib.concatMap (
                project:
                project.renderers.withPackages {
                  python = pkgs.python3;
                  groups = [ "dev" ];
                } ps
              ) (lib.attrValues projects)
            ))
            pkgs.ruff
            pkgs.basedpyright
          ];
        };
      });

      nixosModules.eunomia = import ./eunomia/module.nix self;

      formatter = forAllSystems (pkgs: pkgs.nixfmt-tree);
    };
}
