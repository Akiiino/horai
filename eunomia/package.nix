{ python3Packages }:
python3Packages.buildPythonApplication {
  pname = "eunomia";
  version = "0.1.0";
  pyproject = true;
  src = ./.;

  build-system = [ python3Packages.setuptools ];

  dependencies = [
    python3Packages.python-telegram-bot
    python3Packages.apscheduler
  ];

  nativeCheckInputs = [ python3Packages.pytestCheckHook ];

  meta = {
    description = "A routine-keeping personal assistant driven by a Telegram bot";
    mainProgram = "eunomia";
  };
}
