function tests = test_brainvision_readers
%TEST_BRAINVISION_READERS  BrainVision through bva-io and through FieldTrip give the same data.
%   Phase M (docs/BLUEPRINT_PHASES.md section 11): M1 reads BrainVision with FieldTrip; when
%   EEGLAB's bva-io plugin is installed, both readers must agree on the canonical recording
%   (eegunify.read_any and load_canonical). Needs EEGUNIFY_TEST_VHDR (set by
%   tests/integration/test_matlab.py); the bva-io tests are skipped without the plugin.
tests = functiontests(localfunctions);
end

function setupOnce(tc)
vhdr = getenv('EEGUNIFY_TEST_VHDR');
tc.assumeNotEmpty(vhdr, 'EEGUNIFY_TEST_VHDR not set');
tc.TestData.vhdr = vhdr;
tc.TestData.ft = eegunify.read_any(vhdr, 'brainvision', struct('brainvision_reader', 'fieldtrip'));
end

function testReadAnyBothReadersAgree(tc)
tc.assumeEqual(exist('pop_loadbv', 'file'), 2, 'bva-io not installed');
ft = tc.TestData.ft;
bv = eegunify.read_any(tc.TestData.vhdr, 'brainvision', struct('brainvision_reader', 'bvaio'));
verifyEqual(tc, bv.labels, ft.labels);
verifyEqual(tc, bv.srate, ft.srate);
verifyEqual(tc, size(bv.data), size(ft.data));
verifyLessThan(tc, max(abs(bv.data(:) - ft.data(:))), 1e-3);  % µV, float32 on disk
verifyEqual(tc, {bv.markers.description}, {ft.markers.description});
verifyEqual(tc, [bv.markers.sample], [ft.markers.sample]);
verifyEqual(tc, bv.reader, 'bvaio (brainvision)');
end

function testLoadCanonicalBothPaths(tc)
tc.assumeEqual(exist('pop_loadbv', 'file'), 2, 'bva-io not installed');
EEG = load_canonical(tc.TestData.vhdr);  % bva-io when installed
ft = tc.TestData.ft;
verifyEqual(tc, {EEG.chanlocs.labels}, ft.labels);
verifyLessThan(tc, max(abs(double(EEG.data(:)) - ft.data(:))), 1e-3);
end

function testBvaioMissingIsAClearError(tc)
tc.assumeEqual(exist('pop_loadbv', 'file'), 0, 'bva-io installed: nothing to check');
verifyError(tc, @() eegunify.read_any(tc.TestData.vhdr, 'brainvision', ...
    struct('brainvision_reader', 'bvaio')), 'eegunify:read');
end
