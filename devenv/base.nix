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

    # The steps that make the test suite runnable: install the dependencies of
    # the project. `devenv test` runs them before `check`, so CI needs no
    # separate install step.
    testSetup = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [ "uv sync --extra dev" ];
      description = "Commands that `devenv test` runs before `check`, to install the dependencies of the project. Empty means the suite needs no setup.";
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

    # The release script sits beside this module, so a published copy of the
    # module carries it too.
    base.commands.deploy = lib.mkIf cfg.release.enable [
      ''BASE_RELEASE_BRANCH=${lib.escapeShellArg cfg.release.branch} ${pkgs.bash}/bin/bash ${./release.sh} "$@"''
    ];

    # Language modules append to base.commands, so a project mixing Go and Node
    # gets both languages' work from one `check`.
    scripts = lib.mapAttrs mkTask cfg.commands;

    # `devenv test` is what CI runs: the same setup and the same suite.
    enterTest = lib.mkIf (cfg.commands.check != [ ]) ''
      set -eu
      ${lib.concatMapStringsSep "\n" (c: c) cfg.testSetup}
      check
    '';

    env = {
      BASE_STACKS = lib.concatStringsSep "," cfg.stacks;
      BASE_TDD_GUARD = cfg.tddGuard;
      BASE_PRE_COMMIT = lib.concatStringsSep "\n" cfg.preCommit;
    };

    enterShell = ''
      if [ -n "''${BASE_STACKS:-}" ]; then
        printf '\033[2m%s\033[0m \033[2m·\033[0m \033[2m%s\033[0m\n' \
          "''${DEVENV_ROOT##*/}" "$BASE_STACKS"
      fi
    '';
  };
}
