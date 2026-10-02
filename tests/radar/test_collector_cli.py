"""新的采集入口与旧命令的兼容行为。"""

from click.testing import CliRunner

from stock_robot.cli import main


def test_daemon_stays_available_when_automatic_disabled(mocker):
    worker = mocker.patch('radar.collector_service.build_collector').return_value
    result = CliRunner().invoke(main, ['radar', 'daemon'])
    assert result.exit_code == 0, result.output
    worker.serve.assert_called_once()


def test_once_uses_persistent_queue(mocker):
    worker = mocker.patch('radar.collector_service.build_collector').return_value
    worker.enqueue_manual.return_value = [{'id': 'task', 'universe_id': 'pool'}]
    worker.execute_one.side_effect = [True, False]
    worker.store.get_run.return_value = {'status': 'completed', 'snapshot_run_id': 'snapshot'}
    result = CliRunner().invoke(main, ['radar', 'collect', '--once'])
    assert result.exit_code == 0, result.output
    assert 'pool' in result.output
    worker.enqueue_manual.assert_called_once()


def test_status_reports_settings_without_starting_worker(mocker):
    worker = mocker.patch('radar.collector_service.build_collector').return_value
    worker.store.settings.return_value = {'enabled': False, 'hour': 18, 'minute': 30}
    worker.store.runtime.return_value = {'status': 'never'}
    result = CliRunner().invoke(main, ['radar', 'collector-status'])
    assert result.exit_code == 0
    assert '18:30' in result.output
    worker.serve.assert_not_called()
