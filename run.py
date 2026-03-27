#!/usr/bin/env python3
import argparse
import os
import shutil

from bids import BIDSLayout

from tracula import run_cmd, participant_level, group_level_motion_stats, group_level_tract_pathstats

__version__ = open('/version').read()

parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter,
                                 description='BIDS App for TRACULA processing stream. '
                                             'https://surfer.nmr.mgh.harvard.edu/fswiki/Tracula')
parser.add_argument('bids_dir', help='The directory with the input dataset '
                                     'formatted according to the BIDS standard.')
parser.add_argument('output_dir', help='The directory where the output files '
                                       'should be stored. If you are running group level analysis '
                                       'this folder should be prepopulated with the results of the '
                                       'participant level analysis.')
parser.add_argument('analysis_level', help='Level of the analysis that will be performed. '
                                           '"participant": runs FreeSurfer and reconstructs paths (trac-all -prep, '
                                           '-bedp and -path), '
                                           '"group1": collects motion stats in one file, '
                                           '"group2": collects tract stats in one file.',
                    choices=['participant', 'group1', 'group2'])
parser.add_argument('--license_file', help='Path to FreeSurfer license file (license.txt). '
                                           'If not provided, the license is expected at '
                                           '$FREESURFER_HOME/license.txt. Get a free license at '
                                           'https://surfer.nmr.mgh.harvard.edu/registration.html',
                    default=None)
parser.add_argument('--participant_label',
                    help='The label of the participant that should be analyzed. The label '
                         'corresponds to sub-<participant_label> from the BIDS spec '
                         '(so it does not include "sub-"). If this parameter is not '
                         'provided all subjects should be analyzed. Multiple '
                         'participants can be specified with a space separated list.', nargs="+")
parser.add_argument('--session_label',
                    help='The label of the sessions that should be analyzed. The label '
                         'corresponds to ses-<session_label> from the BIDS spec '
                         '(so it does not include "ses-"). If this parameter is not '
                         'provided all sessions should be analyzed. Multiple '
                         'sessions can be specified with a space separated list.', nargs="+")

parser.add_argument('--freesurfer_dir', help='The directory with the FreeSurfer data. If not specified, '
                                             'FreeSurfer data is written into output_dir. If FreeSurfer '
                                             'data cannot be found for a subject, this app will run FreeSurfer as '
                                             'well.')
parser.add_argument('--stages', help='Participant-level trac-all stages to run. Passing '
                                     '"all" will run "prep", "bedp" and "path".',
                    choices=["prep", "bedp", "path", "all"], default=["all"], nargs="+")
parser.add_argument('--n_cpus', help='Number of CPUs/cores available to use.', default=1, type=int)
parser.add_argument('-v', '--version', action='version',
                    version='TRACULA BIDS-App version {}'.format(__version__))


args = parser.parse_args()

# Validate FreeSurfer license
fs_home = os.environ.get('FREESURFER_HOME', '/opt/freesurfer-8.0.0')
license_path = os.path.join(fs_home, 'license.txt')
if args.license_file:
    shutil.copy2(args.license_file, license_path)
if not os.path.exists(license_path):
    parser.error('FreeSurfer license not found. Either:\n'
                 '  1. Mount it: -v /path/to/license.txt:' + license_path + '\n'
                 '  2. Pass it: --license_file /path/to/license.txt\n'
                 '  Register for free at https://surfer.nmr.mgh.harvard.edu/registration.html')

if not args.freesurfer_dir:
    args.freesurfer_dir = args.output_dir

if not os.path.exists(args.output_dir):
    os.makedirs(args.output_dir)

layout = BIDSLayout(args.bids_dir)

if args.participant_label:
    subjects_to_analyze = args.participant_label
else:
    subjects_to_analyze = layout.get_subjects()

if args.analysis_level == "participant":
    participant_level(args, layout, subjects_to_analyze, args.session_label)

elif args.analysis_level == "group1":
    group_level_motion_stats(args, subjects_to_analyze)

elif args.analysis_level == "group2":
    group_level_tract_pathstats(args, subjects_to_analyze)
