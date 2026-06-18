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
    in
    {
      packages = forAllSystems (pkgs: {
        eunomia = pkgs.callPackage ./eunomia/package.nix { };
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: [
              ps.python-telegram-bot
              ps.apscheduler
              ps.pytest
            ]))
            pkgs.ruff
          ];
        };
      });

      nixosModules.eunomia = import ./eunomia/module.nix self;

      formatter = forAllSystems (pkgs: pkgs.nixfmt);
    };
}
