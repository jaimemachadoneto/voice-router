{
  pkgs,
  lib,
  config,
  ...
}:
let
  cfg = config.base;

  # Every verb always exists, even with nothing wired up for the project's
  # stack, so `check` or `run` is never a "command not found" surprise.
  mkTask = verb: cmds: {
    exec =
      if cmds == [ ] then
        ''
          printf '\033[2mnothing wired up for `%s` in this project\033[0m\n' ${lib.escapeShellArg verb}
        ''
      else
        ''
          set -eu
          ${lib.concatMapStringsSep "\n" (c: ''
            printf '\033[1;34m▸\033[0m %s\n' ${lib.escapeShellArg c}
            ${c}
          '') cmds}
        '';
  };

  taskOption =
    verb: description:
    lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      inherit description;
      example = [ "echo hello" ];
    };
in
{
  options.base = {
    # The five verbs every project answers are declared so they always exist and
    # carry docs. Any other name a project sets (`base.commands.deploy`) becomes
    # a command of the same name, so a per-repo verb needs no change here.
    commands = lib.mkOption {
      default = { };
      description = "Shell commands per verb. Each attribute becomes a script of the same name.";
      type = lib.types.submodule {
        freeformType = lib.types.attrsOf (lib.types.listOf lib.types.str);
        options = {
          fmt = taskOption "fmt" "Shell commands run by `fmt`: format all code in place.";
          lint = taskOption "lint" "Shell commands run by `lint`: static analysis and type checks.";
          check = taskOption "check" "Shell commands run by `check`: the test suite. Named `check` rather than `test` because `test` is a POSIX shell builtin and would shadow the script.";
          build = taskOption "build" "Shell commands run by `build`: produce build artifacts.";
          run = taskOption "run" "Shell commands run by `run`: start the application.";
          deploy = taskOption "deploy" "Shell commands run by `deploy`. Set `base.release.enable` to make it start a release; a project with its own pipeline leaves it empty.";
        };
      };
    };

    stacks = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "Language stacks active in this project. Each language module appends its own name; used by the shell banner and `base list`.";
    };

    # `deploy` on a project that CI releases: it tags the release branch and
    # pushes the tag, and the GitHub workflows build and publish. Projects with
    # a pipeline of their own (GitLab at work) leave this off.
    release = {
      enable = lib.mkEnableOption "`deploy` as a release: `deploy patch|minor|major` tags the release branch as vX.Y.Z and pushes the tag";
      branch = lib.mkOption {
        type = lib.types.str;
        default = "main";
        description = "The only branch `deploy` releases from.";
      };
    };

    # The edit checks of the agent harness plugins (`base hook`) read this.
    tddGuard = lib.mkOption {
      type = lib.types.enum [
        "off"
        "warn"
        "block"
      ];
      default = "warn";
      description = "The TDD guard for agent edits: `warn` reminds the agent when production code changes with no test file changed, `block` refuses the edit, `off` does nothing.";
    };

    # Verbs that run before each commit, whatever made the change: a person,
    # Claude Code, OpenCode. The shell writes .git/hooks/pre-commit for them.
    preCommit = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [ "lint" ];
      description = "Commands (usually verbs such as `lint`) that `git commit` runs first. A failure stops the commit. Empty means no hook.";
    };

    # Python is a tool here, not a stack: agents and one-off scripts reach for
    # it to do work in projects written in something else entirely. Turn it off
    # in a project that must prove it needs nothing but its own toolchain.
    scriptingPython =
      lib.mkEnableOption "a bare CPython interpreter for ad-hoc scripting, in projects of any language"
      // {
        default = true;
      };
  };

  config = {
    # Only when the python stack is absent: that module owns the interpreter,
    # and a second python3 in the same profile would collide with its venv.
    # macOS ships a /usr/bin/python3 that is an Xcode stub and only errors, so
    # without this there is no working interpreter in a non-python project.
    # Nothing is installed into it — libraries mean the python stack and a
    # pyproject.toml, not `pip install`.
    # gh lets `deploy` follow the CI run it started.
    packages =
      lib.optional (cfg.scriptingPython && !config.languages.python.enable) pkgs.python3
      ++ lib.optional cfg.release.enable pkgs.gh;

    # The release script sits beside this module, so a vendored copy of the
    # module carries it too.
    base.commands.deploy = lib.mkIf cfg.release.enable [
      ''BASE_RELEASE_BRANCH=${lib.escapeShellArg cfg.release.branch} ${pkgs.bash}/bin/bash ${./release.sh} "$@"''
    ];

    # Language modules append to base.commands, so a project mixing Go and Node
    # gets both languages' work from one `check`.
    scripts = lib.mapAttrs mkTask cfg.commands;

    # `devenv test` runs the same suite CI does.
    enterTest = lib.mkIf (cfg.commands.check != [ ]) ''
      check
    '';

    env = {
      BASE_STACKS = lib.concatStringsSep "," cfg.stacks;
      BASE_TDD_GUARD = cfg.tddGuard;
      BASE_PRE_COMMIT = lib.concatStringsSep "\n" cfg.preCommit;
    }
    # OpenCode reads the workspace config through the environment: the
    # instructions (AGENTS.md) and the generated subagents in opencode/agents.
    # A vendored copy of this module has no opencode/ beside it and skips this.
    // lib.optionalAttrs (builtins.pathExists ../opencode/opencode.json) {
      BASE_ROOT = toString ./..;
      OPENCODE_CONFIG = "${toString ./..}/opencode/opencode.json";
      OPENCODE_CONFIG_DIR = "${toString ./..}/opencode";
    };

    enterShell = ''
      # The memini namespace of this project, for agents that cannot ask
      # memini themselves (OpenCode reads it in opencode.json). It is the repo
      # name from the git remote, the same rule that memini uses.
      MEMINI_NAMESPACE=$(basename "$(git remote get-url origin 2>/dev/null)" .git 2>/dev/null)
      export MEMINI_NAMESPACE="''${MEMINI_NAMESPACE:-$(basename "$PWD")}"

      # `base` on PATH in every project shell, so `base doctor` or a re-link
      # works without returning to the workspace root. toString keeps this a
      # real filesystem path rather than copying the tree into the nix store.
      case ":$PATH:" in
        *":${toString ./..}/bin:"*) ;;
        *) export PATH="${toString ./..}/bin:$PATH" ;;
      esac

      # The shell function passes the generated presets to dsh, so ~/.dsh stays
      # untouched. It passes the patch only for the tui and web profiles.
      if [ -n "''${BASE_ROOT:-}" ] && [ -f "$BASE_ROOT/dsh/agents.patch.yml" ]; then
        dsh() {
          local profile="tui" rest=()
          case "''${1:-}" in
            "") ;;
            --profile)
              profile="''${2:-tui}"
              rest=("''${@:3}")
              ;;
            *)
              profile="$1"
              rest=("''${@:2}")
              ;;
          esac
          case "$profile" in
            tui | web)
              # The dsh MCP rows read these variables, so a token never goes
              # into the generated patch file.
              MEMINI_API_KEY="$(cat "$HOME/.config/memini/api-key" 2>/dev/null)" \
                LITELLM_API_KEY="$(cat "$HOME/.config/litellm/api-key" 2>/dev/null)" \
                command dsh "$profile" --patch "$BASE_ROOT/dsh/agents.patch.yml" "''${rest[@]}"
              ;;
            *) command dsh "$@" ;;
          esac
        }
      fi

      # Keep the shared Claude config current on entry. This relinks only
      # .claude/, never a devenv file: nothing under .claude is a devenv input
      # or a direnv watch, so it cannot retrigger the shell it runs in. Stack
      # wiring still needs an explicit `base sync`, since devenv.local.nix is
      # a watched input. Set BASE_NO_SYNC=1 to skip.
      if [ -z "''${BASE_NO_SYNC:-}" ] && [ -x "${toString ./..}/bin/base" ]; then
        # devenv does not forward a child process's stdout from enterShell, so
        # capture the report and print it with the shell's own printf.
        base_msg="$("${toString ./..}/bin/base" sync-claude "$DEVENV_ROOT" 2>/dev/null || true)"
        [ -n "$base_msg" ] && printf '%s\n' "$base_msg"
        unset base_msg
      fi

      # The git pre-commit hook for base.preCommit. Written only into a hook
      # file that base owns, never over a hook the project has.
      if [ -z "''${BASE_NO_SYNC:-}" ] && [ -x "${toString ./..}/bin/base" ]; then
        base_msg="$("${toString ./..}/bin/base" git-hook "$DEVENV_ROOT" 2>&1 || true)"
        [ -n "$base_msg" ] && printf '%s\n' "$base_msg"
        unset base_msg
      fi

      if [ -n "''${BASE_STACKS:-}" ]; then
        printf '\033[2m%s\033[0m \033[2m·\033[0m \033[2m%s\033[0m\n' \
          "''${DEVENV_ROOT##*/}" "$BASE_STACKS"
      fi
    '';
  };
}
