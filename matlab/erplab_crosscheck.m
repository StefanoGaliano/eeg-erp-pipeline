function result = erplab_crosscheck(setfile, eventsfile, outjson, p)
%ERPLAB_CROSSCHECK  ERP-lite through ERPLAB on data eegunify already preprocessed (M-E5).
%
%   result = erplab_crosscheck(setfile, eventsfile, outjson, p)
%
%   setfile    EEGLAB .set of eegunify's preprocessed data (filtered, re-referenced),
%              written by tests/integration/test_erplab_crosscheck.py.
%   eventsfile TSV with columns sample (1-based) and code: the stimulus events.
%   outjson    where the result is written.
%   p          struct: epoch_ms [-200 800], baseline_ms [-200 0], ptp_uv, window_ms,
%              step_ms, measure_ms [110 150], channel 'PO8', eeg_channels (cellstr of
%              the channels pop_artmwppth tests), bins (cell of
%              {label, '{codes}'} pairs, e.g. {'face', '1-40'}), bin_suffix (optional
%              sequencer appended to every bin, e.g. '{t<200-1000>201}').
%
%   Steps (docs/BLUEPRINT_PHASES.md section 8.4): event list -> binlister -> pop_epochbin
%   -> pop_artmwppth on the EEG channels (and pop_artstep on HEOG when p.step_uv > 0, with
%   p.heog, p.step_window_ms, p.step_step_ms) -> pop_averager (good trials) -> pop_binoperator
%   (bin1 - bin2) -> pop_geterpvalues ('meanbl'). The result holds the rejected epochs as
%   1-based indices into the events file, the trials per bin and the mean amplitudes.
%   Needs EEGLAB with ERPLAB (Practice/setup_eeg_toolboxes.m).

ev = readtable(eventsfile, 'FileType', 'text', 'Delimiter', '\t');
EEG = pop_loadset('filename', setfile);
% Numeric event codes, as ERPLAB's binlister expects (the .set carries trial-type labels).
EEG.event = struct('type', num2cell(ev.code(:)'), 'latency', num2cell(ev.sample(:)'), ...
    'duration', num2cell(zeros(1, height(ev))), 'urevent', num2cell(1:height(ev)));
EEG.urevent = rmfield(EEG.event, 'urevent');
EEG = eeg_checkset(EEG, 'eventconsistency');

[result, ~] = eegunify.erplab_erp(EEG, p);
fid = fopen(outjson, 'w', 'n', 'UTF-8');
fprintf(fid, '%s', jsonencode(result));
fclose(fid);
end
