function result = erp_lite(vhdr, dumpPath, outDir)
%ERP_LITE  ERP-lite in MATLAB from canonical files (Phase M2, BLUEPRINT_PHASES section 11).
%
%   result = eegunify.erp_lite(vhdr, 'dump.json', outDir)
%
%   vhdr      canonical recording (written by eegunify.convert or by Python)
%   dump.json `eegunify config dump <yaml> --json --resolved -s <sub>` of the config with the
%             erp_lite recipe applied (see tests/integration/test_matlab_erp_lite.py)
%   outDir    receives preproc.set (filtered, re-referenced continuous data, float32),
%             erp.mat (bindata channels x times x waves, times_ms, labels, waves) and
%             erp_lite.json (= result).
%
%   The chain, as the Python stages with the erp_lite recipe:
%     1. bipolar EOG channels (artifacts.eog_bipolar, a minus b, in name order);
%     2. ERPLAB pop_basicfilter, Butterworth, zero phase; preproc.filter.iir_order is the
%        total forward-backward order, as ERPLAB's 'Order';
%     3. re-reference the EEG channels (average of the good EEG channels, or
%        reference.channels); bad channels stay in the data, outside the average;
%     4. eegunify.erplab_erp on the first component: bins = the plus and minus conditions of
%        its contrast, the response rule as an ERPLAB sequencer, pop_artmwppth on the good
%        EEG channels, pop_artstep on HEOG, mean amplitude over the primary ROI.
%   Steps the recipe switches off (resampling, interpolation, ICA, line noise) are refused
%   rather than approximated.
cfg = eegunify.config_load(dumpPath);
c = cfg.config;
check_scope(c);
if ~isfolder(outDir), mkdir(outDir); end

[EEG, events] = load_canonical(vhdr);
channels = read_tsv(strrep(vhdr, '_eeg.vhdr', '_channels.tsv'));
labels = {EEG.chanlocs.labels};
types = lower(cellstr(string(channels.type)))';
assert(isequal(labels, cellstr(string(channels.name))'), 'eegunify:erp_lite', ...
    'channels.tsv and the data disagree on channel order');
bads = EEG.etc.eegunify.bad_channels;

% 1. Bipolar EOG.
derived = {};
if ~isempty(c.artifacts.eog_bipolar)
    bip = c.artifacts.eog_bipolar;
    for name = sort(fieldnames(bip))'
        pair = cellstr(bip.(name{1}));
        a = strcmp(labels, pair{1}); b = strcmp(labels, pair{2});
        assert(any(a) && any(b), 'eegunify:erp_lite', 'eog_bipolar %s: channels not found', name{1});
        x = EEG.data(a, :) - EEG.data(b, :);
        old = strcmp(labels, name{1});
        EEG.data(old, :) = []; labels(old) = []; types(old) = [];
        EEG.data(end + 1, :) = x;
        labels{end + 1} = name{1}; %#ok<AGROW>
        types{end + 1} = 'eog'; %#ok<AGROW>
        derived{end + 1} = name{1}; %#ok<AGROW>
    end
    EEG.nbchan = size(EEG.data, 1);
    EEG.chanlocs = struct('labels', labels);
    EEG = eeg_checkset(EEG);
end

% 2. Filter (ERPLAB).
eegunify.erplab_path();
pre = c.preproc;
hp = pre.highpass_hz; lp = pre.lowpass_hz;
if ~isempty(hp) || ~isempty(lp)
    if ~isempty(hp) && ~isempty(lp)
        kind = 'bandpass'; cutoff = [hp lp];
    elseif ~isempty(hp)
        kind = 'highpass'; cutoff = hp;
    else
        kind = 'lowpass'; cutoff = lp;
    end
    EEG = pop_basicfilter(EEG, 1:EEG.nbchan, 'Filter', kind, 'Design', 'butter', ...
        'Cutoff', cutoff, 'Order', pre.filter.iir_order, 'RemoveDC', 'off');
end

% 3. Re-reference the EEG channels.
iseeg = strcmp(types, 'eeg');
switch c.reference.scheme
    case 'average'
        refIdx = iseeg & ~ismember(labels, bads);
    case 'channels'
        refIdx = ismember(labels, cellstr(c.reference.channels));
    otherwise
        error('eegunify:erp_lite', 'reference.scheme %s is not in ERP-lite', c.reference.scheme);
end
targets = iseeg;
if isfield(c.reference, 'apply_to_eog') && c.reference.apply_to_eog
    targets = targets | (strcmp(types, 'eog') & ~ismember(labels, derived));
end
EEG.data(targets, :) = EEG.data(targets, :) - mean(EEG.data(refIdx, :), 1);
pop_saveset(EEG, 'filename', 'preproc.set', 'filepath', outDir, 'savemode', 'twofiles', ...
    'version', '7');  % v7: readable by scipy / MNE

% 4. Epochs, rejection, averages, measures (ERPLAB).
comps = fieldnames(cfg.components);
assert(~isempty(comps), 'eegunify:erp_lite', 'no component in the config dump (--resolved?)');
comp = cfg.components.(comps{1});  % definition fields plus key, overridden
contrast = c.contrasts.(comp.contrast);
eset = c.epochs.sets.(comp.epoch_set);
rej = c.epochs.reject;

codes = cellfun(@str2double, cellstr(string(events.value)));
tt = cellstr(string(events.trial_type));
tags = strcat(tt, '/', cellstr(string(events.value)));
EEG.event = struct('type', num2cell(codes(:)'), ...
    'latency', num2cell(events.onset(:)' * EEG.srate + 1), ...
    'duration', num2cell(zeros(1, numel(codes))), 'urevent', num2cell(1:numel(codes)));
EEG.urevent = rmfield(EEG.event, 'urevent');
EEG = eeg_checkset(EEG, 'eventconsistency');

waves = {contrast.plus, contrast.minus};
bins = cell(1, 2);
for w = 1:2
    sel = cellstr(c.conditions.(waves{w}));
    hit = matches_any(tags, sel) & matches_any(tags, {eset.lock});
    bins{w} = {waves{w}, code_list(codes(hit))};
end
suffix = '';
if ~isempty(eset.response)
    hit = matches_any(tags, {eset.response.selector});
    suffix = sprintf('{t<%g-%g>%s}', eset.response.window_ms(1), eset.response.window_ms(2), ...
        code_list(codes(hit)));
end
baseline = eset.baseline_ms;
if isempty(baseline), baseline = [eset.tmin_ms 0]; end
p = struct('epoch_ms', [eset.tmin_ms eset.tmax_ms], 'baseline_ms', baseline(:)', ...
    'ptp_uv', rej.ptp_uv, 'window_ms', rej.ptp_window_ms, 'step_ms', rej.ptp_step_ms, ...
    'measure_ms', comp.window_ms(:)', 'bin_suffix', suffix, ...
    'step_uv', 0, 'heog', 'HEOG', 'step_window_ms', rej.step_window_ms, ...
    'step_step_ms', rej.step_step_ms);
p.channel = cellstr(comp.rois.primary)';
p.eeg_channels = labels(iseeg & ~ismember(labels, bads));
p.bins = bins;
if ~isempty(rej.step_heog_uv) && any(strcmpi(labels, 'HEOG')), p.step_uv = rej.step_heog_uv; end

[result, ERP] = eegunify.erplab_erp(EEG, p);
result.rejected_samples = events.sample(result.rejected_events)';  % 0-based, as events.tsv
result.waves = [waves, {comp.contrast}];
result.component = comps{1};
result.config_hash = cfg.config_hash;
result.reference_channels = labels(refIdx);

bindata = ERP.bindata; %#ok<NASGU>
times_ms = ERP.times; %#ok<NASGU>
erp_labels = {ERP.chanlocs.labels}; %#ok<NASGU>
erp_waves = result.waves; %#ok<NASGU>
save(fullfile(outDir, 'erp.mat'), 'bindata', 'times_ms', 'erp_labels', 'erp_waves', '-v7');
fid = fopen(fullfile(outDir, 'erp_lite.json'), 'w', 'n', 'UTF-8');
fprintf(fid, '%s', jsonencode(result));
fclose(fid);
end

function check_scope(c)
pre = c.preproc;
msg = 'ERP-lite refuses %s (apply the erp_lite recipe before dumping the config)';
assert(strcmp(c.artifacts.method, 'none'), 'eegunify:erp_lite', msg, 'artifacts.method');
assert(isempty(pre.resample_hz), 'eegunify:erp_lite', msg, 'preproc.resample_hz');
assert(~pre.interpolate, 'eegunify:erp_lite', msg, 'preproc.interpolate');
assert(strcmp(pre.line_noise.method, 'none'), 'eegunify:erp_lite', msg, 'preproc.line_noise');
assert(strcmp(pre.bad_channels.method, 'config_only'), 'eegunify:erp_lite', msg, ...
    'automatic bad-channel detection');
assert(strcmp(pre.filter.method, 'iir') && strcmp(pre.filter.phase, 'zero'), ...
    'eegunify:erp_lite', msg, 'a filter other than IIR zero-phase');
assert(strcmp(c.epochs.reject.method, 'thresholds'), 'eegunify:erp_lite', msg, ...
    'epochs.reject.method other than thresholds');
assert(isempty(c.epochs.reject.flat_uv), 'eegunify:erp_lite', msg, 'epochs.reject.flat_uv');
assert(isempty(c.epochs.reject.rules), 'eegunify:erp_lite', msg, 'epochs.reject.rules');
end

function hit = matches_any(tags, selectors)
% eegunify.erp.events.matches: the label's '/'-parts include every selector part ('*' = any).
hit = false(size(tags));
for s = 1:numel(selectors)
    parts = strsplit(selectors{s}, '/');
    parts = parts(~cellfun(@isempty, parts) & ~strcmp(parts, '*'));
    for k = 1:numel(tags)
        hit(k) = hit(k) || all(ismember(parts, strsplit(tags{k}, '/')));
    end
end
end

function s = code_list(codes)
s = strjoin(arrayfun(@(x) sprintf('%d', x), unique(codes(:)'), 'UniformOutput', false), ';');
end

function t = read_tsv(path)
opts = detectImportOptions(path, 'FileType', 'text', 'Delimiter', '\t');
opts = setvartype(opts, intersect(opts.VariableNames, {'name', 'type', 'status'}), 'string');
t = readtable(path, opts);
end
