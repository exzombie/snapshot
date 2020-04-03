from snapshot.core import SnapshotError, SnapshotPv
import os
import re
import json
import glob
import numpy


save_file_suffix = '.snap'


class SnapshotReqFile(object):
    def __init__(self, path: str, parent=None, macros: dict = None, changeable_macros: list = None):
        """
        Class providing parsing methods for request files.

        :param path: Request file path.
        :param parent: SnapshotReqFile from which current file was called.
        :param macros: Dict of macros {macro: value}
        :param changeable_macros: List of "global" macros which can stay unreplaced and will be handled by
                                  Shanpshot object (enables user to change macros on the fly). This macros will be
                                  ignored in error handling.

        :return:
        """
        if macros is None:
            macros = dict()
        if changeable_macros is None:
            changeable_macros = list()

        self._path = os.path.abspath(path)
        self._parent = parent
        self._macros = macros
        self._c_macros = changeable_macros

        if parent:
            self._trace = '{} [line {}: {}] >> {}'.format(parent._trace, parent._curr_line_n, parent._curr_line,
                                                          self._path)
        else:
            self._trace = self._path

        self._curr_line = None
        self._curr_line_n = 0
        self._curr_line_txt = ''
        self._err = list()

    def read(self):
        """
        Parse request file and return list of pv names where changeable_macros are not replaced. ("raw" pv names).
        In case of problems raises exceptions.
                ReqParseError
                    ReqFileFormatError
                    ReqFileInfLoopError

        :return: List of PV names.
        """
        f = open(self._path)

        pvs = list()
        err = list()

        self._curr_line_n = 0
        for self._curr_line in f:
            self._curr_line_n += 1
            self._curr_line = self._curr_line.strip()

            # skip comments and empty lines
            if not self._curr_line.startswith(('#', "data{", "}", "!")) and self._curr_line.strip():
                # First replace macros, then check if any unreplaced macros which are not "global"
                pvname = SnapshotPv.macros_substitution((self._curr_line.rstrip().split(',', maxsplit=1)[0]),
                                                        self._macros)

                try:
                    # Check if any unreplaced macros
                    self._validate_macros_in_txt(pvname)
                except MacroError as e:
                    f.close()
                    raise ReqParseError(self._format_err((self._curr_line_n, self._curr_line), e))

                pvs.append(pvname)

            elif self._curr_line.startswith('!'):
                # Calling another req file
                split_line = self._curr_line[1:].split(',', maxsplit=1)

                if len(split_line) > 1:
                    macro_txt = split_line[1].strip()
                    if not macro_txt.startswith(('\"', '\'')):
                        f.close()
                        raise ReqFileFormatError(self._format_err((self._curr_line_n, self._curr_line),
                                                                  'Syntax error. Macros argument must be quoted.'))
                    else:
                        quote_type = macro_txt[0]

                    if not macro_txt.endswith(quote_type):
                        f.close()
                        raise ReqFileFormatError(self._format_err((self._curr_line_n, self._curr_line),
                                                                  'Syntax error. Macros argument must be quoted.'))

                    macro_txt = SnapshotPv.macros_substitution(macro_txt[1:-1], self._macros)
                    try:
                        self._validate_macros_in_txt(macro_txt)  # Check if any unreplaced macros
                        macros = parse_macros(macro_txt)

                    except MacroError as e:
                        f.close()
                        raise ReqParseError(self._format_err((self._curr_line_n, self._curr_line), e))

                else:
                    macros = dict()

                path = os.path.join(os.path.dirname(self._path), split_line[0])
                msg = self._check_looping(path)
                if msg:
                    f.close()
                    raise ReqFileInfLoopError(self._format_err((self._curr_line_n, self._curr_line), msg))

                try:
                    sub_f = SnapshotReqFile(path, parent=self, macros=macros)
                    sub_pvs = sub_f.read()
                    pvs += sub_pvs

                except IOError as e:
                    f.close()
                    raise IOError(self._format_err((self._curr_line, self._curr_line_n), e))
        f.close()
        return pvs

    def _format_err(self, line: tuple, msg: str):
        return '{} [line {}: {}]: {}'.format(self._trace, line[0], line[1], msg)

    def _validate_macros_in_txt(self, txt: str):
        invalid_macros = list()
        macro_rgx = re.compile('\$\(.*?\)')  # find all of type $()
        raw_macros = macro_rgx.findall(txt)
        for raw_macro in raw_macros:
            if raw_macro not in self._macros.values() and raw_macro[2:-1] not in self._c_macros:
                # There are unknown macros which were not substituted
                invalid_macros.append(raw_macro)

        if invalid_macros:
            raise MacroError('Following macros were not defined: {}'.format(', '.join(invalid_macros)))

    def _check_looping(self, path):
        path = os.path.normpath(os.path.abspath(path))
        ancestor = self  # eventually could call self again

        while ancestor is not None:
            if os.path.normpath(os.path.abspath(ancestor._path)) == path:
                if ancestor._parent:
                    msg = 'Infinity loop detected. File {} was already called from {}'.format(path,
                                                                                              ancestor._parent._path)
                else:
                    msg = 'Infinity loop detected. File {} was already loaded as root request file.'.format(path)

                return msg
            else:
                ancestor = ancestor._parent


# Helper functions functions to support macros parsing for users of this lib
def parse_macros(macros_str):
    """
    Converting comma separated macros string to dictionary.

    :param macros_str: string of macros in style SYS=TST,D=A

    :return: dict of macros
    """

    macros = dict()
    if macros_str:
        macros_list = macros_str.split(',')
        for macro in macros_list:
            split_macro = macro.strip().split('=')
            if len(split_macro) == 2:
                macros[split_macro[0]] = split_macro[1]
            else:
                raise MacroError('Following string cannot be parsed to macros: {}'.format(macros_str))
    return macros

class MacroError(SnapshotError):
    """
    Problems parsing macros (wrong syntax).
    """
    pass


class ReqParseError(SnapshotError):
    """
    Parent exception class for exceptions that can happen while parsing a request file.
    """
    pass


class ReqFileFormatError(ReqParseError):
    """
    Syntax error in request file.
    """
    pass


class ReqFileInfLoopError(ReqParseError):
    """
    If request file is calling one of its ancestors.
    """
    pass


def initialize_config(config_path=None, save_dir=None, force=False,
                      default_labels=None, force_default_labels=None,
                      req_file_path=None, req_file_macros=None,
                      init_path=None):
    """
    Settings are a dictionary which holds common configuration of
    the application (such as directory with save files, request file
    path, etc). It is propagated to snapshot widgets.

    :param save_dir: path to the default save directory
    :param config_path: path to configuration file
    :param force: force saving on disconnected channels
    :param default_labels: list of default labels
    :param force_default_labels: whether user can only select predefined labels
    :param req_file_path: path to request file
    :param req_file_macros: macros can be as dict (key, value pairs)
                            or a string in format A=B,C=D
    :param init_path: default path to be shown on the file selector
    """
    config = {'config_ok': True, 'macros_ok': True}
    if config_path:
        # Validate configuration file
        try:
            config.update(json.load(open(config_path)))
            # force-labels must be type of bool
            if not isinstance(config.get('labels', dict())
                                    .get('force-labels', False), bool):
                raise TypeError('"force-labels" must be boolean')
        except Exception as e:
            # Propagate error to the caller, but continue filling in defaults
            config['config_ok'] = False
            config['config_error'] = str(e)

    config['save_file_prefix'] = ''
    config['req_file_path'] = ''
    config['req_file_macros'] = dict()
    config['existing_labels'] = list()  # labels that are already in snap files
    config['force'] = force
    config['init_path'] = init_path if init_path else ''

    if isinstance(default_labels, str):
        default_labels = default_labels.split(',')

    elif not isinstance(default_labels, list):
        default_labels = list()

    # default labels also in config file? Add them
    config['default_labels'] = \
        list(set(default_labels + (config.get('labels', dict())
                                   .get('labels', list()))))

    config['force_default_labels'] = \
        config.get('labels', dict()) \
              .get('force-labels', False) or force_default_labels

    # Predefined filters
    config["predefined_filters"] = config.get('filters', dict())

    if req_file_macros is None:
        req_file_macros = dict()
    elif isinstance(req_file_macros, str):
        # Try to parse macros. If problem, just pass to configure window
        # which will force user to do it right way.
        try:
            req_file_macros = parse_macros(req_file_macros)
        except MacroError:
            config['macros_ok'] = False
        config['req_file_macros'] = req_file_macros

    if req_file_path and config['macros_ok']:
        config['req_file_path'] = \
            os.path.abspath(os.path.join(config['init_path'], req_file_path))

    if not save_dir:
        # Default save dir (do this once we have valid req file)
        save_dir = os.path.dirname(config['req_file_path'])

    if not save_dir:
        config['save_dir'] = None
    else:
        config['save_dir'] = os.path.abspath(save_dir)

    return config


def parse_from_save_file(save_file_path, metadata_only=False):
    """
    Parses save file to dict {'pvname': {'data': {'value': <value>, 'raw_name': <name_with_macros>}}}

    :param save_file_path: Path to save file.

    :return: (saved_pvs, meta_data, err)

        saved_pvs: in format {'pvname': {'data': {'value': <value>, 'raw_name': <name_with_macros>}}}

        meta_data: as dictionary

        err: list of strings (each entry one error)
    """

    saved_pvs = dict()
    meta_data = dict()  # If macros were used they will be saved in meta_data
    err = list()
    saved_file = open(save_file_path)
    meta_loaded = False

    for line in saved_file:
        # first line with # is metadata (as json dump of dict)
        if line.startswith('#') and not meta_loaded:
            line = line[1:]
            try:
                meta_data = json.loads(line)
            except json.JSONDecodeError:
                # Problem reading metadata
                err.append('Meta data could not be decoded. '
                           'Must be in JSON format.')
            meta_loaded = True
            if metadata_only:
                break
        # skip empty lines and all rest with #
        elif (not metadata_only
                and line.strip()
                and not line.startswith('#')):
            split_line = line.strip().split(',', 1)
            pvname = split_line[0]
            if len(split_line) > 1:
                pv_value_str = split_line[1]
                # In case of array it will return a list, otherwise value
                # of proper type
                try:
                    pv_value = json.loads(pv_value_str)
                except json.JSONDecodeError:
                    pv_value = None
                    err.append('Value of \'{}\' cannot be decoded. '
                               'Will be ignored.'.format(pvname))

                if isinstance(pv_value, list):
                    # arrays as numpy array, because pyepics returns
                    # as numpy array
                    pv_value = numpy.asarray(pv_value)
            else:
                pv_value = None

            saved_pvs[pvname] = dict()
            saved_pvs[pvname]['value'] = pv_value

    if not meta_loaded:
        err.insert(0, 'No meta data in the file.')

    saved_file.close()
    return saved_pvs, meta_data, err


def get_save_files(save_dir, req_file_path, current_files):
    """
    Parses all new or modified files. Parsed files are returned as a
    dictionary.
    """
    parsed_save_files = dict()
    err_to_report = list()
    req_file_name = os.path.basename(req_file_path)
    # Check if any file added or modified (time of modification)
    file_dir = os.path.join(save_dir, os.path.splitext(req_file_name)[0])
    file_list = glob.glob(file_dir + '*' + save_file_suffix)
    for file_path in file_list:
        file_name = os.path.basename(file_path)
        if os.path.isfile(file_path):
            already_known = file_name in current_files
            modif_time = os.path.getmtime(file_path)
            if already_known:
                modified = modif_time != current_files[file_name]["modif_time"]
            if not already_known or modified:
                _, meta_data, err = parse_from_save_file(file_path,
                                                         metadata_only=True)

                # Check if we have req_file metadata. This is used to determine
                # which request file the save file belongs to. If there is no
                # metadata (or no req_file specified in the metadata) we search
                # using a prefix of the request file. The latter is less
                # robust, but is backwards compatible.
                have_metadata = "req_file_name" in meta_data \
                    and meta_data["req_file_name"] == req_file_name
                prefix_matches = \
                    file_name.startswith(req_file_name.split(".")[0] + "_")
                if have_metadata or prefix_matches:
                    # we really should have basic meta data
                    # (or filters and some other stuff will silently fail)
                    if "comment" not in meta_data:
                        meta_data["comment"] = ""
                    if "labels" not in meta_data:
                        meta_data["labels"] = []

                    parsed_save_files[file_name] = {
                        'file_name': file_name,
                        'file_path': file_path,
                        'meta_data': meta_data,
                        'modif_time': modif_time
                    }

                    if err:  # report errors only for matching saved files
                        err_to_report.append((file_name, err))

    return parsed_save_files, err_to_report
