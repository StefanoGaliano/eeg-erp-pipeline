function tests = test_eegunify_package
%TEST_EEGUNIFY_PACKAGE  matlab.unittest tests for matlab/+eegunify on small inputs (no data).
%   Run: runtests('matlab/tests') with matlab/ on the path. The data-level parity checks are
%   tests/integration/test_matlab_parity.py (Python drives MATLAB).
tests = functiontests(localfunctions);
end

function setupOnce(tc)
t.standard_1005 = {'Fp1', 'Fp2', 'Cz', 'T7', 'P7', 'O1', 'Oz', 'Fz', 'AF7'};
t.biosemi64 = [{'Fp1', 'AF7'}, compose('X%d', 3:64)];
t.aliases = struct('from', {'T3', 'T5'}, 'to', {'T7', 'P7'});
t.rename = struct('from', {}, 'to', {});
t.event_mapping = struct('key', {'1-40', '201', 'Comment/two  words'}, ...
    'trial_type', {'stimulus/face', 'response/correct', 'stimulus/label'});
t.onset_shift_ms = struct('pattern', {'stimulus/*'}, 'ms', {26});
tc.TestData.tables = t;
c.events = struct('source', 'auto', 'stim_channel', [], 'ignore_codes', 0, ...
    'unmapped', 'error');
c.format.hints.bdf_status_mask = 255;
tc.TestData.cfg = struct('config', c, 'matlab', t);
end

function testNamesVendorCaseAliases(tc)
names = eegunify.normalize_names({'EEG FP1-Ref', 'cz', 'T3', 'EOG HEOG', 'Status'}, ...
    tc.TestData.tables, {'Status'});
verifyEqual(tc, names, {'Fp1', 'Cz', 'T7', 'HEOG', 'Status'});
end

function testNamesBiosemiPositional(tc)
labels = [compose('A%d', 1:32), compose('B%d', 1:32)];
names = eegunify.normalize_names(labels, tc.TestData.tables);
verifyEqual(tc, names(1:2), {'Fp1', 'AF7'});
end

function testNamesCollisionIsError(tc)
verifyError(tc, @() eegunify.normalize_names({'Fp1', 'FP1'}, tc.TestData.tables), ...
    'eegunify:names');
end

function testTypes(tc)
std = tc.TestData.tables.standard_1005;
verifyEqual(tc, eegunify.infer_type('Status', std), 'stim');
verifyEqual(tc, eegunify.infer_type('VEOG_lower', std), 'eog');
verifyEqual(tc, eegunify.infer_type('Cz', std), 'eeg');
verifyEqual(tc, eegunify.infer_type('A12', std), 'eeg');
verifyEqual(tc, eegunify.infer_type('EXG1', std, 'eeg'), 'misc');
end

function testStimEventsMaskedAndShifted(tc)
% Status with a high bit always set (masked away), pulses of codes 3 and 201.
rec = stim_rec([0 0 3 3 0 0 201 201 0], 65536);
cfg = tc.TestData.cfg;
[ev, method] = eegunify.extract_events(rec, cfg);
verifyEqual(tc, method, 'stim_channel:Status');
% 26 ms at 1000 Hz moves the face from sample 2 to 28, after the (unshifted) response at 6:
% the table is re-sorted by onset.
verifyEqual(tc, ev.value', {'201', '3'});
verifyEqual(tc, ev.trial_type', {'response/correct', 'stimulus/face'});
verifyEqual(tc, ev.sample', [6 28]);
end

function testUnmappedPolicies(tc)
rec = stim_rec([0 77 0], 0);
cfg = tc.TestData.cfg;
verifyError(tc, @() eegunify.extract_events(rec, cfg), 'eegunify:events');
cfg.config.events.unmapped = 'drop';
verifyEqual(tc, height(eegunify.extract_events(rec, cfg)), 0);
cfg.config.events.unmapped = 'keep_as_code';
ev = eegunify.extract_events(rec, cfg);
verifyEqual(tc, ev.trial_type, {'77'});
end

function testMarkersLabelsAndHousekeeping(tc)
rec = stim_rec([], 0);
rec.labels = {'Cz'}; rec.reader_types = {'eeg'}; rec.data = zeros(1, 10);
rec.markers = struct('sample', {0, 4, 6}, 'duration', {0, 0, 0}, ...
    'description', {'New Segment/', 'Stimulus/S 201', 'Comment/two words'});
ev = eegunify.extract_events(rec, tc.TestData.cfg);
% "New Segment/" is housekeeping; a BrainVision marker is parsed to its code; a text label
% matches its key with whitespace collapsed.
verifyEqual(tc, ev.value', {'201', 'Comment/two words'});
verifyEqual(tc, ev.trial_type', {'response/correct', 'stimulus/label'});
end

function rec = stim_rec(codes, highBits)
rec.labels = {'Cz', 'Status'};
rec.reader_types = {'eeg', 'stim'};
rec.data = [zeros(1, numel(codes)); codes + highBits];
rec.srate = 1000;
rec.status_mask = 255;
rec.markers = struct('sample', {}, 'duration', {}, 'description', {});
end
