function tests = test_load_canonical
%TEST_LOAD_CANONICAL matlab.unittest tests for load_canonical.m.
%   Needs EEGLAB (+ FieldTrip or bva-io) on the path and a converted recording:
%     setenv('EEGUNIFY_TEST_VHDR', '.../sub-015_ses-N170_task-N170_eeg.vhdr')
%     results = runtests('matlab/tests')
%   Run from Python with `pytest -m realdata` (skipped when MATLAB is absent).
tests = functiontests(localfunctions);
end

function setupOnce(tc)
vhdr = getenv('EEGUNIFY_TEST_VHDR');
tc.assumeNotEmpty(vhdr, 'EEGUNIFY_TEST_VHDR not set');
tc.TestData.vhdr = vhdr;
[tc.TestData.EEG, tc.TestData.events] = load_canonical(vhdr);
end

function testEventsComeFromEventsTsv(tc)
EEG = tc.TestData.EEG; events = tc.TestData.events;
verifyEqual(tc, numel(EEG.event), height(events));
verifyEqual(tc, {EEG.event.type}, cellstr(string(events.trial_type))');
verifyEqual(tc, {EEG.event.code}, cellstr(string(events.value))');
end

function testLatencyIsOneBased(tc)
EEG = tc.TestData.EEG; events = tc.TestData.events;
verifyEqual(tc, [EEG.event.latency], (events.onset' * EEG.srate) + 1, 'AbsTol', 1e-6);
end

function testShape(tc)
EEG = tc.TestData.EEG;
verifyEqual(tc, size(EEG.data), [EEG.nbchan, EEG.pnts]);
verifyGreaterThan(tc, EEG.srate, 0);
verifyTrue(tc, iscell(EEG.etc.eegunify.bad_channels));
end
