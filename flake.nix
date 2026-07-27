{
  description = "A Nix-flake-based Go development environment";

  inputs.nixpkgs.url = "https://flakehub.com/f/NixOS/nixpkgs/0.1"; # unstable Nixpkgs

  outputs =
    { self, ... }@inputs:

    let
      goVersion = 26; # Change this to update the whole stack

      supportedSystems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];
      forEachSupportedSystem =
        f:
        inputs.nixpkgs.lib.genAttrs supportedSystems (
          system:
          f {
            inherit system;
            pkgs = import inputs.nixpkgs {
              inherit system;
              overlays = [ inputs.self.overlays.default ];
            };
          }
        );
    in
    {
      overlays.default = final: prev: {
        go = final."go_1_${toString goVersion}";
      };

      devShells = forEachSupportedSystem (
        { pkgs, system }:
        {
          default = pkgs.mkShellNoCC {
            packages = with pkgs; [
              go # (version is specified by overlay)
              gotools # goimports, godoc, etc.
              golangci-lint # https://github.com/golangci/golangci-lint

              nodejs_26 # web/ frontend (React + TS + Vite); npm bundled

              duckdb
              (python3.withPackages (ps: with ps; [
                duckdb # read books.duckdb, hash, parquet
                pyarrow # write parquet parts (fixed_size_list<float32,4096>)
                numpy # L2-normalize vectors
                requests # talk to llama-server /v1/embeddings
              ]))
              # llama-cpp # (just use homebrew version for now)

              self.formatter.${system}
            ];
          };
        }
      );

      formatter = forEachSupportedSystem ({ pkgs, ... }: pkgs.nixfmt);
    };
}
