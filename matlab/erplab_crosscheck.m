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
%              {label, '{codes}'} pairs, e.g. {'face', '1-40'}).
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

bdf = [tempname '.txt'];
fid = fopen(bdf, 'w');
for b = 1:numel(p.bins)
    fprintf(fid, 'bin %d\n%s\n.{%s}\n\n', b, p.bins{b}{1}, p.bins{b}{2});
end
fclose(fid);
cleanup = onCleanup(@() delete(bdf));

EEG = pop_creabasiceventlist(EEG, 'AlphanumericCleaning', 'on', 'BoundaryNumeric', {-99}, ...
    'BoundaryString', {'boundary'}, 'Warning', 'off');
EEG = pop_binlister(EEG, 'BDF', bdf, 'IndexEL', 1, 'SendEL2', 'EEG', 'Voutput', 'EEG', ...
    'Warning', 'off');
EEG = pop_epochbin(EEG, p.epoch_ms, p.baseline_ms);

eeg = find(ismember({EEG.chanlocs.labels}, p.eeg_channels));
EEG = pop_artmwppth(EEG, 'Channel', eeg, 'Flag', 1, 'Threshold', p.ptp_uv, ...
    'Twindow', [EEG.xmin EEG.xmax] * 1000, 'Windowsize', p.window_ms, ...
    'Windowstep', p.step_ms, 'Review', 'off');
if isfield(p, 'step_uv') && p.step_uv > 0
    heog = find(strcmpi({EEG.chanlocs.labels}, p.heog));
    EEG = pop_artstep(EEG, 'Channel', heog, 'Flag', 1, 'Threshold', p.step_uv, ...
        'Twindow', [EEG.xmin EEG.xmax] * 1000, 'Windowsize', p.step_window_ms, ...
        'Windowstep', p.step_step_ms, 'Review', 'off');
end

% Which events became rejected epochs: each epoch's time-locking event is its 'urevent'.
flags = EEG.reject.rejmanual;
locking = zeros(1, EEG.trials);
for i = 1:EEG.trials
    lat0 = [EEG.epoch(i).eventlatency{:}];
    ure = [EEG.epoch(i).eventurevent{:}];
    if iscell(lat0), lat0 = cell2mat(lat0); end
    locking(i) = ure(find(lat0 == 0, 1));
end

ERP = pop_averager(EEG, 'Criterion', 'good', 'ExcludeBoundary', 'on', 'SEM', 'on');
nb = numel(p.bins);
ERP = pop_binoperator(ERP, {sprintf('b%d = b1 - b2 label %s_minus_%s', nb + 1, ...
    p.bins{1}{1}, p.bins{2}{1})});
ch = find(strcmpi({ERP.chanlocs.labels}, p.channel));
[~, amp] = pop_geterpvalues(ERP, p.measure_ms, 1:nb + 1, ch, 'Measure', 'meanbl', ...
    'Filename', 'no_save.no_save', 'Warning', 'off');

result = struct();
result.erplab_version = geterplabversion;
result.srate = EEG.srate;
result.epoch_samples = EEG.pnts;
result.epoch_xmin_ms = EEG.xmin * 1000;
result.rejected_events = sort(locking(flags == 1));
result.n_epochs = EEG.trials;
result.trials_per_bin = ERP.ntrials.accepted;
result.mean_uv = amp(:)';
result.labels = {ERP.bindescr{:}};
fid = fopen(outjson, 'w', 'n', 'UTF-8');
fprintf(fid, '%s', jsonencode(result));
fclose(fid);
end
