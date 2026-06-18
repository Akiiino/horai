self:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.eunomia;
in
{
  options.services.eunomia = {
    enable = lib.mkEnableOption "Eunomia routine assistant";

    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.eunomia;
      defaultText = lib.literalExpression "horai.packages.\${system}.eunomia";
      description = "The Eunomia package to run.";
    };

    stateDir = lib.mkOption {
      type = lib.types.str;
      default = "/var/lib/eunomia";
      description = "Directory holding the SQLite history and the routine file.";
    };

    routineFile = lib.mkOption {
      type = lib.types.str;
      default = "${cfg.stateDir}/routine.toml";
      defaultText = lib.literalExpression "\"\${stateDir}/routine.toml\"";
      description = ''
        Path to the routine.toml. A writable path (not the Nix store) so it can
        be edited live; the daemon seeds a default if it is missing and
        hot-reloads on change.
      '';
    };

    timezone = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = config.time.timeZone or null;
      defaultText = lib.literalExpression "config.time.timeZone";
      description = "IANA timezone for block times. Null = system local time.";
    };

    tokenFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      example = "/run/agenix/eunomia";
      description = ''
        Path to a file containing the bare Telegram bot token. Exposed to the
        service via systemd LoadCredential, so it works with DynamicUser and an
        agenix secret with default (root-only) ownership.
      '';
    };

    chatId = lib.mkOption {
      type = lib.types.nullOr lib.types.int;
      default = null;
      example = 123456789;
      description = ''
        Telegram chat id to send nudges to. Not a secret. If null, the bot
        adopts the first chat that messages it (message it once to bind).
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.services.eunomia = {
      description = "Eunomia routine assistant";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];

      environment = {
        EUNOMIA_DB = "${cfg.stateDir}/eunomia.db";
        EUNOMIA_ROUTINE = cfg.routineFile;
      }
      // lib.optionalAttrs (cfg.timezone != null) { EUNOMIA_TZ = cfg.timezone; }
      // lib.optionalAttrs (cfg.chatId != null) { TELEGRAM_CHAT_ID = toString cfg.chatId; };

      serviceConfig = {
        ExecStart = lib.getExe cfg.package;
        LoadCredential = lib.mkIf (cfg.tokenFile != null) [ "token:${cfg.tokenFile}" ];
        Restart = "on-failure";
        RestartSec = 5;

        DynamicUser = true;
        StateDirectory = "eunomia";
        WorkingDirectory = cfg.stateDir;

        # hardening
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        NoNewPrivileges = true;
        ProtectKernelTunables = true;
        ProtectControlGroups = true;
        RestrictAddressFamilies = [
          "AF_INET"
          "AF_INET6"
        ];
        SystemCallFilter = [ "@system-service" ];
      };
    };
  };
}
