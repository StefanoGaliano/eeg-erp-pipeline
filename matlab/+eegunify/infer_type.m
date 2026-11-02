function t = infer_type(name, std, readerType)
%INFER_TYPE  Channel type from its name, as eegunify.channels.types.infer_type (Python).
%
%   Name rules first (stim, EOG, EMG, ECG, resp, temperature), then 10-05 names and
%   positional cap labels (BioSemi A1, EGI E12) -> eeg, then the reader's type if it is
%   specific, else misc.
if nargin < 3, readerType = ''; end
rules = {
    '^(status|trig(ger)?|sti\d*|stim.*|marker)$', 'stim'
    '(eog|heog|veog|^lo[12]$|^io[12]$|^so[12]$)', 'eog'
    'emg', 'emg'
    '(ecg|ekg)', 'ecg'
    '^(resp|breath)', 'resp'
    '^(temp)', 'temperature'};
for r = 1:size(rules, 1)
    if ~isempty(regexpi(name, rules{r, 1}, 'once'))
        t = rules{r, 2};
        return
    end
end
if any(strcmp(std, name)) || ~isempty(regexp(name, '^([A-H]([1-9]|[12]\d|3[0-2])|E\d{1,3})$', 'once'))
    t = 'eeg';
elseif ~isempty(readerType) && ~any(strcmp(readerType, {'eeg', 'misc'}))
    t = readerType;
else
    t = 'misc';
end
end
