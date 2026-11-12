{
  description = "order_trees: file-mirrored mappings";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f (import nixpkgs { inherit system; }));
    in
    {
      packages = forAll (pkgs: {
        default = pkgs.python3Packages.buildPythonPackage {
          pname = "order_trees";
          version = "0.1.0";
          src = ./.;
          pyproject = true;
          build-system = with pkgs.python3Packages; [ setuptools ];
          dependencies = [ ];
          nativeCheckInputs = with pkgs.python3Packages; [ pytest ];
          doCheck = false;
        };
      });

      checks = forAll (pkgs: {
        pytest =
          let
            pythonEnv = pkgs.python3.withPackages (ps: [ ps.pytest ps.orjson ]);
          in
          pkgs.stdenv.mkDerivation {
            name = "order_trees-pytest";
            src = ./.;
            nativeBuildInputs = [ pythonEnv ];
            doCheck = true;
            checkPhase = ''
              pytest -q
            '';
            installPhase = ''
              mkdir -p $out
              echo ok > $out/result
            '';
          };
      });

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: [
              ps.pytest
              ps.orjson
              ps.setuptools
            ]))
          ];
          shellHook = ''
            export PYTHONPATH="$PWD''${PYTHONPATH:+:}$PYTHONPATH"
          '';
        };
      });
    };
}
