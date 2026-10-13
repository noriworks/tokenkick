"""CLI surface shared by people, terminal menus and MCP agents."""
import click

from . import stagger
from .app_mode import emit_app_error, emit_app_success
from .consent import auto_kick_consent_text


@click.group("stagger")
def stagger_group():
    """Maintain an ongoing Gemini/Claude-GPT 2h30m stagger in the daemon."""


@stagger_group.command("status")
@click.option("--json-output", is_flag=True)
def stagger_status(json_output):
    from . import cli
    result = cli._stagger_status_payload()
    if json_output:
        emit_app_success(result)
    else:
        stagger.render(cli.console, result)
        if result.get("mode") == "disabled" and not result.get("members"):
            click.echo("Ongoing stagger is not configured. Use tk stagger preview --help.")


def options(function):
    function = click.option("--claude", help="Saved Antigravity Claude/GPT account label (enable only).")(function)
    function = click.option("--gemini", help="Saved Antigravity Gemini account label (enable only).")(function)
    return click.option("--action", type=click.Choice(["enable", "resume", "pause", "disable", "recover"]), required=True)(function)


@stagger_group.command("preview")
@options
@click.option("--json-output", is_flag=True)
def stagger_preview(action, gemini, claude, json_output):
    from . import cli
    try:
        result = stagger.preview(action, gemini, claude, directory=cli.CONFIG_DIR)
        result["consent_text"] = auto_kick_consent_text("antigravity") if action in {"enable", "resume"} else None
        if json_output:
            emit_app_success(result)
        else:
            click.echo(f"{action.title()} persistent Antigravity stagger (2h30m).")
            for change in result["changes"]:
                click.echo(f"{change['label']}: master {change['master_before']} → {change['master_after']}; session {change['session_before']} → {change['session_after']}")
            click.echo(result["note"])
            if result["consent_text"]:
                click.echo(result["consent_text"])
            click.echo("Preview fingerprint: " + result["fingerprint"])
    except stagger.StaggerError as exc:
        if json_output:
            emit_app_error("stagger_preview_failed", str(exc))
            raise click.exceptions.Exit(1) from exc
        raise click.ClickException(str(exc)) from exc


@stagger_group.command("apply")
@options
@click.option("--expected", required=True, help="Fingerprint from the exact preview.")
@click.option("--yes", is_flag=True, help="Confirm the previewed settings change.")
@click.option("--accept-risk", type=click.Choice(["ENABLE"]), help="Acknowledge recurring quota use and provider disclosure.")
@click.option("--json-output", is_flag=True)
def stagger_apply(action, gemini, claude, expected, yes, accept_risk, json_output):
    from . import cli
    try:
        if not yes:
            raise stagger.StaggerError("Review tk stagger preview, then supply --yes to apply.")
        stagger.apply(action, gemini, claude, expected=expected,
                      accept_risk=accept_risk == "ENABLE", directory=cli.CONFIG_DIR)
        result = cli._stagger_status_payload()
        if json_output:
            emit_app_success(result)
        else:
            stagger.render(cli.console, result)
    except stagger.StaggerError as exc:
        if json_output:
            emit_app_error("stagger_apply_failed", str(exc))
            raise click.exceptions.Exit(1) from exc
        raise click.ClickException(str(exc)) from exc
