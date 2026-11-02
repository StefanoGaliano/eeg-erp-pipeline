function [result, ERP, EEG] = erplab_erp(EEG, p)
%ERPLAB_ERP  Epochs, rejection, averages and mean amplitudes through ERPLAB (M-E5 and M2).
%
%   [result, ERP, EEG] = eegunify.erplab_erp(EEG, p)
%
%   EEG  continuous EEGLAB set whose EEG.event types are the numeric event codes, in the order
%        of the event list the caller indexes (result.rejected_events refers to it, 1-based).
%   p    struct: epoch_ms [-200 800], baseline_ms [-200 0], ptp_uv, window_ms, step_ms,
%        measure_ms [110 150], channel (one name, or a cellstr ROI whose mean is measured),
%        eeg_channels (cellstr: the channels pop_artmwppth tests), bins (cell of
%        {label, 'codes'} pairs, e.g. {'face', '1-40'}), bin_suffix (optional sequencer
%        after every bin, e.g. '{t<200-1000>201}'), step_uv/heog/step_window_ms/step_step_ms
%        (pop_artstep on HEOG when step_uv > 0).
%
%   Steps (docs/BLUEPRINT_PHASES.md section 8.4): event list -> binlister -> pop_epochbin
%   -> pop_artmwppth -> pop_artstep -> pop_averager (good trials) -> pop_binoperator
%   (bin1 - bin2) -> pop_geterpvalues ('meanbl'). Needs EEGLAB with ERPLAB.

eegunify.erplab_path();

% p.bin_suffix: optional sequencer after the time-locking codes, e.g. '{t<200-1000>201}'
% (ERP CORE's correct-response bins).
suffix = '';
if isfield(p, 'bin_suffix'), suffix = p.bin_suffix; end
bdf = [tempname '.txt'];
fid = fopen(bdf, 'w');
for b = 1:numel(p.bins)
    fprintf(fid, 'bin %d\n%s\n.{%s}%s\n\n', b, p.bins{b}{1}, p.bins{b}{2}, suffix);
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

% Which events became rejected epochs: ERPLAB's EVENTLIST keeps one item per original event
% (same order as the events file) and its 'bepoch', the epoch made from it (0 = none).
flags = EEG.reject.rejmanual;
bepoch = [EEG.EVENTLIST.eventinfo.bepoch];
items = find(bepoch > 0);
rejected = items(flags(bepoch(items)) == 1);

ERP = pop_averager(EEG, 'Criterion', 'good', 'ExcludeBoundary', 'on', 'SEM', 'on');
nb = numel(p.bins);
ERP = pop_binoperator(ERP, {sprintf('b%d = b1 - b2 label %s_minus_%s', nb + 1, ...
    p.bins{1}{1}, p.bins{2}{1})});
% A ROI is measured channel by channel and averaged (mean amplitude is linear, so this
% equals measuring the ROI-mean wave, as eegunify does).
roi = cellstr(p.channel);
amp = zeros(nb + 1, numel(roi));
for r = 1:numel(roi)
    ch = find(strcmpi({ERP.chanlocs.labels}, roi{r}));
    assert(numel(ch) == 1, 'eegunify:erplab', 'channel %s not in the data', roi{r});
    [~, a] = pop_geterpvalues(ERP, p.measure_ms, 1:nb + 1, ch, 'Measure', 'meanbl', ...
        'Filename', 'no_save.no_save', 'Warning', 'off');
    amp(:, r) = a(:);
end
amp = mean(amp, 2);

result = struct();
result.erplab_version = geterplabversion;
result.srate = EEG.srate;
result.epoch_samples = EEG.pnts;
result.epoch_xmin_ms = EEG.xmin * 1000;
result.rejected_events = sort(rejected);
result.n_epochs = EEG.trials;
result.trials_per_bin = ERP.ntrials.accepted;
result.mean_uv = amp(:)';
result.labels = {ERP.bindescr{:}};
end
