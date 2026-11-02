function violations = canonical_validate(rec, cfg)
%CANONICAL_VALIDATE  The canonical invariants (BLUEPRINT section 5), as eegunify.canonical.
%
%   warnings = eegunify.canonical_validate(rec, cfg)
%
%   Raises eegunify:canonical listing every error-level violation; returns the warnings as a
%   struct array (rule, level, message). rec is the struct built by eegunify.convert.
%   Rule 6 (electrode positions) is not checked: Phase M writes no electrodes.tsv.
%   Rule 8 (measurement date) is handled at write time, as in Python.
v = struct('rule', {}, 'level', {}, 'message', {});
units = cfg.config.units;
rng = units.expected_eeg_range_uV(:)';
% 1. Finite data, plausible amplitude (refuse only unmistakable scale errors).
if any(~isfinite(rec.data(:)))
    v(end + 1) = viol(1, 'error', 'data contain NaN or Inf');
end
[sd, factor] = unit_verdict(rec, rng);
if factor ~= 0
    level = 'warning';
    far = sd > rng(2) * 10 || sd < rng(1) / 10;
    if factor ~= 1 && far, level = 'error'; end
    v(end + 1) = viol(1, level, sprintf('implausible EEG amplitude: robust SD %.3g uV', sd));
end
% 2. EEG names are 10-05 or positional.
std = cfg.matlab.standard_1005;
for k = find(strcmp(rec.types, 'eeg'))
    n = rec.names{k};
    if ~any(strcmp(std, n)) && isempty(regexp(n, '^([A-H]\d{1,3}|E\d{1,3})$', 'once'))
        v(end + 1) = viol(2, 'warning', sprintf('EEG channel ''%s'' is not a 10-05 name', n)); %#ok<AGROW>
    end
end
% 3. Every channel typed, no stim left, some EEG.
allowed = {'eeg', 'eog', 'emg', 'ecg', 'misc', 'resp', 'temperature'};
for k = find(~ismember(rec.types, allowed))
    v(end + 1) = viol(3, 'error', sprintf('channel ''%s'' has type ''%s''', ...
        rec.names{k}, rec.types{k})); %#ok<AGROW>
end
if ~any(strcmp(rec.types, 'eeg')), v(end + 1) = viol(3, 'error', 'no EEG channels'); end
% 4. Events carry their original code.
if any(cellfun(@isempty, rec.events.value))
    v(end + 1) = viol(4, 'error', 'event without original code');
end
if height(rec.events) == 0, v(end + 1) = viol(4, 'warning', 'recording has no events'); end
% 5. Online reference.
if isempty(rec.reference) || strcmp(rec.reference, 'n/a')
    v(end + 1) = viol(5, 'warning', 'online reference unknown (set reference.online)');
end
% 7. Bad channels exist.
for b = rec.bads
    if ~any(strcmp(rec.names, b{1}))
        v(end + 1) = viol(7, 'error', sprintf('bad channel ''%s'' is not in the recording', b{1})); %#ok<AGROW>
    end
end
% 9. Provenance.
if isempty(rec.source_files), v(end + 1) = viol(9, 'error', 'provenance has no source files'); end
% 10. No resampling.
if abs(rec.srate - rec.source_srate) > 1e-9
    v(end + 1) = viol(10, 'error', 'sampling rate changed during ingestion');
end
isErr = strcmp({v.level}, 'error');
if any(isErr)
    msg = strjoin(arrayfun(@(x) sprintf('  [rule %d] %s', x.rule, x.message), v(isErr), ...
        'UniformOutput', false), newline);
    error('eegunify:canonical', 'recording is not canonical:\n%s', msg);
end
violations = v(~isErr);
end

function x = viol(rule, level, message)
x = struct('rule', rule, 'level', level, 'message', message);
end

function [sd, factor] = unit_verdict(rec, rng)
% eegunify.units: median over good EEG channels of 1.4826*MAD on the first 60 s.
% factor = 0 when plausible, else the 10^k suggestion (1 when none fits).
pick = strcmp(rec.types, 'eeg') & ~ismember(rec.names, rec.bads);
stop = min(size(rec.data, 2), floor(60 * rec.srate));
x = rec.data(pick, 1:stop);
x = x - median(x, 2);
mad = median(abs(x), 2) * 1.4826;
mad = mad(mad > 0);
if isempty(mad), sd = 0; else, sd = median(mad); end
factor = 0;
if ~isfinite(sd) || sd == 0
    factor = 1;
elseif sd < rng(1) || sd > rng(2)
    factor = 1;
    for f = [1e-6, 1e-3, 1e3, 1e6]
        if sd * f >= rng(1) && sd * f <= rng(2), factor = f; break, end
    end
end
end
