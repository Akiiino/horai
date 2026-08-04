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
    enable = lib.mkEnableOption "Eunomia routine-to-do adapter";

    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.eunomia;
      defaultText = lib.literalExpression "horai.packages.\${system}.eunomia";
      description = "The Eunomia package to run.";
    };

    caldavUrl = lib.mkOption {
      type = lib.types.str;
      example = "https://dav.example.org/";
      description = "Base URL of the CalDAV server (RustiCal).";
    };

    caldavUser = lib.mkOption {
      type = lib.types.str;
      description = "CalDAV username (principal) Eunomia authenticates as.";
    };

    passwordFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      example = "/run/agenix/horai-rustical";
      description = ''
        Path to a file containing the CalDAV app password. Exposed to the
        service via systemd LoadCredential, so it works with DynamicUser and an
        agenix secret with default (root-only) ownership.
      '';
    };

    routineCalendar = lib.mkOption {
      type = lib.types.str;
      default = "Routine";
      description = "Name of the CalDAV calendar holding the routine VEVENTs.";
    };

    todoCalendar = lib.mkOption {
      type = lib.types.str;
      default = "Eunomia";
      description = "Name of the CalDAV task list the derived to-dos are kept in.";
    };

    windowAhead = lib.mkOption {
      type = lib.types.str;
      default = "48h";
      description = "How long before a block starts its to-dos appear.";
    };

    windowBack = lib.mkOption {
      type = lib.types.str;
      default = "12h";
      description = "How long after a block ends its to-dos are removed.";
    };

    stepStagger = lib.mkOption {
      type = lib.types.str;
      default = "1s";
      description = ''
        How far apart a block's steps are placed, so the client sorts them in
        order and their notifications arrive in that order. Raise it if a client
        rounds the steps together and scrambles them.
      '';
    };

    poll = lib.mkOption {
      type = lib.types.str;
      default = "5m";
      description = ''
        Longest nap between checks for edits to the routine calendar. Window
        edges are hit exactly regardless — this only bounds how stale a
        just-edited routine can be.
      '';
    };

    timezone = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = config.time.timeZone or null;
      defaultText = lib.literalExpression "config.time.timeZone";
      description = "IANA timezone for floating event times. Null = system local time.";
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.services.eunomia = {
      description = "Eunomia routine-to-do adapter";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];

      environment = {
        EUNOMIA_CALDAV_URL = cfg.caldavUrl;
        EUNOMIA_CALDAV_USER = cfg.caldavUser;
        EUNOMIA_CALDAV_ROUTINE = cfg.routineCalendar;
        EUNOMIA_CALDAV_TODOS = cfg.todoCalendar;
        EUNOMIA_WINDOW_AHEAD = cfg.windowAhead;
        EUNOMIA_WINDOW_BACK = cfg.windowBack;
        EUNOMIA_STEP_STAGGER = cfg.stepStagger;
        EUNOMIA_POLL = cfg.poll;
      }
      // lib.optionalAttrs (cfg.timezone != null) { EUNOMIA_TZ = cfg.timezone; };

      serviceConfig = {
        ExecStart = lib.getExe cfg.package;
        LoadCredential = lib.mkIf (cfg.passwordFile != null) [ "caldav-password:${cfg.passwordFile}" ];
        Restart = "on-failure";
        RestartSec = 5;

        # No StateDirectory and no WorkingDirectory: the adapter keeps nothing
        # between passes, and the calendar server owns every byte it touches.
        DynamicUser = true;

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
