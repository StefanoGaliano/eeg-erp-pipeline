function [names, renameLog] = normalize_names(labels, tables, protect)
%NORMALIZE_NAMES  Canonical channel names, same steps as eegunify.channels.naming (Python).
%
%   names = eegunify.normalize_names(labels, cfg.matlab, {'Status'})
%
%   1. BioSemi 64 positional labels (exactly A1..B32) -> 10-20 names (biosemi64 order);
%   2. strip vendor prefixes/suffixes ("EEG Fp1-Ref" -> "Fp1");
%   3. case against the 10-05 list ("FP1" -> "Fp1");
%   4. aliases ("T3" -> "T7"), exact match first, then case-insensitive;
%   5. channels.rename, keyed by the original or the normalized name.
%   The 10-05 list, aliases and BioSemi order come from the config dump, so both languages use
%   the same tables. Two channels with the same final name is an error.
if nargin < 3, protect = {}; end
labels = cellstr(labels);
std = tables.standard_1005;
stdLower = lower(std);
names = labels;

expected = [compose('A%d', 1:32), compose('B%d', 1:32)];
candidates = labels(~ismember(labels, protect));
positional = candidates(~cellfun(@isempty, regexp(candidates, '^[A-H]([1-9]|[12]\d|3[0-2])$')));
usePositional = isequal(sort(positional), sort(expected));

aliasFrom = {tables.aliases.from};
aliasTo = {tables.aliases.to};
renFrom = {tables.rename.from};
renTo = {tables.rename.to};
for k = 1:numel(labels)
    orig = labels{k};
    if ismember(orig, protect)
        new = orig;
    elseif usePositional && ismember(orig, expected)
        new = tables.biosemi64{strcmp(expected, orig)};
    else
        new = strtrim(orig);
        new = regexprep(new, '^(EEG|EOG|EMG|ECG|EKG)[\s_:-]+', '', 'ignorecase');
        new = regexprep(new, '[-_](REF|A1|A2|M1|M2|LE|AVG|CZ|AR)$', '', 'ignorecase');
        new = strtrim(new);
        hit = find(strcmp(stdLower, lower(new)), 1);
        if ~isempty(hit), new = std{hit}; end
        hit = find(strcmp(aliasFrom, new), 1);
        if isempty(hit), hit = find(strcmpi(aliasFrom, new), 1, 'last'); end
        if ~isempty(hit), new = aliasTo{hit}; end
    end
    hit = find(strcmp(renFrom, orig), 1);
    if isempty(hit), hit = find(strcmp(renFrom, new), 1); end
    if ~isempty(hit), new = renTo{hit}; end
    names{k} = new;
end
[u, ~, idx] = unique(names, 'stable');
if numel(u) < numel(names)
    dup = u{find(accumarray(idx(:), 1) > 1, 1)};
    error('eegunify:names', ['channels %s both normalize to %s; add a channels.rename ' ...
        'entry to disambiguate'], strjoin(labels(strcmp(names, dup)), ' and '), dup);
end
renameLog = cell2struct([labels; names], {'original', 'name'}, 1)';
end
