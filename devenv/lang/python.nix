{
  pkgs,
  lib,
  config,
  ...
}:
let
  cfg = config.base.python;
in
{
  imports = [ ../base.nix ];

  options.base.python = {
    version = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "3.12";
      description = "Exact CPython version. Requires the `nixpkgs-python` input in devenv.yaml; null uses the nixpkgs default python3.";
    };
    typeCheck = lib.mkEnableOption "pyright type checking in `lint`" // {
      default = true;
    };
  };

  config = {
    base.stacks = [ "python" ];

    languages.python = {
      enable = true;
      version = lib.mkIf (cfg.version != null) cfg.version;
      venv.enable = true;
      uv.enable = true;
    };

    packages =
      with pkgs;
      [
        ruff
      ]
      ++ lib.optional cfg.typeCheck pyright;

    base.commands = {
      fmt = [ "ruff format ." ];
      lint = [
        "ruff check ."
      ]
      ++ lib.optional cfg.typeCheck "pyright";
      # Run through the venv's interpreter, not a nix-provided pytest, which
      # could not import the project's own dependencies.
      check = [
        "if python -c 'import pytest' 2>/dev/null; then python -m pytest -q; else echo \"pytest is not installed in this project's venv — add it to pyproject.toml dev dependencies\"; fi"
      ];
    };
  };
}
