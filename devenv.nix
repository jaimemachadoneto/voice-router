{ pkgs, ... }:
{
  # The shared stacks. `base link` writes this import; the stacks themselves
  # come from the devbase-stacks flake input in devenv.yaml.
  imports = [ ./devenv.stacks.nix ];

  name = "voice-router";

  # `deploy patch` tags the release and pushes the tag. The release workflow
  # builds the image and opens the PR in home-ops.
  base.release.enable = true;

  # Stacks come from devenv.base.nix (written by `base link`), pulled in by
  # devenv.local.nix. Put project-specific tools here; machine-only settings
  # go in devenv.local.nix.
  packages = with pkgs; [ ];

  # env.DATABASE_URL = "...";
  # services.postgres.enable = true;
}
