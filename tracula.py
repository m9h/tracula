import os
import subprocess
from collections import OrderedDict
from glob import glob
from itertools import product
from subprocess import Popen, PIPE
from warnings import warn
from joblib import Parallel, delayed

import pandas as pd
import shutil

# from https://github.com/BIDS-Apps/freesurfer/blob/master/run.py#L11
def run_cmd(command, env={}, ignore_errors=False):
    merged_env = os.environ
    merged_env.update(env)
    # DEBUG env triggers freesurfer to produce gigabytes of files
    merged_env.pop('DEBUG', None)
    process = Popen(command, stdout=PIPE, stderr=subprocess.STDOUT, shell=True, env=merged_env)
    while True:
        line = process.stdout.readline()
        line = str(line, 'utf-8')[:-1]
        print(line)
        if line == '' and process.poll() != None:
            break
    if process.returncode != 0 and not ignore_errors:
        raise Exception("Non zero return code: %d" % process.returncode)


def get_data(layout, subject_label, freesurfer_dir, truly_longitudinal_study, session_label=""):
    # collect filenames for one subject (and one session if longitudinal)
    # for longitudinal pass session_label
    # raises exception if some data is missing

    # long
    if session_label and truly_longitudinal_study:
        subject_session_info = {"subject": subject_label, "session": session_label}
    # cross
    else:
        subject_session_info = {"subject": subject_label}

    dwi_files = [f.path for f in layout.get(suffix="dwi", datatype="dwi", extension=[".nii", ".nii.gz"],
                                            **subject_session_info)]
    bvecs_files = [f.path for f in layout.get(suffix="dwi", extension=".bvec", **subject_session_info)]
    if not bvecs_files:
        # if bvecs only in root dir
        bvecs_files = [f.path for f in layout.get(suffix="dwi", extension=".bvec")]
    bvals_files = [f.path for f in layout.get(suffix="dwi", extension=".bval", **subject_session_info)]
    if not bvals_files:
        # if bvals only in root dir
        bvals_files = [f.path for f in layout.get(suffix="dwi", extension=".bval")]

    # check if all data is there
    if not dwi_files:
        raise Exception("No DWI files for subject %s %s" % (subject_label, session_label))
    if not bvecs_files:
        raise Exception("No bvec files for subject %s %s" % (subject_label, session_label))
    if not bvals_files:
        raise Exception("No bvals files for subject %s %s" % (subject_label, session_label))

    if session_label and truly_longitudinal_study:
        # long
        freesurfer_subjects = ["sub-{sub}".format(sub=subject_label),
                               "sub-{sub}_ses-{ses}".format(sub=subject_label, ses=session_label),
                               "sub-{sub}_ses-{ses}.long.sub-{sub}".format(sub=subject_label, ses=session_label)]

    else:
        # cross
        freesurfer_subjects = ["sub-{sub}".format(sub=subject_label)]

    for fss in freesurfer_subjects:
        if not os.path.exists(os.path.join(freesurfer_dir, fss, "scripts/recon-all.done")):
            raise Exception("No freesurfer folder for subject %s" % subject_label)

    return dwi_files, bvecs_files, bvals_files


def create_dmrirc(freesurfer_dir, output_dir, subject_label, subject_session_info):
    subject_names = []
    base_names = []
    dwi_files = []
    bvecs_files = []
    bvals_files = []

    for subject_session_name, files in subject_session_info.items():
        n_images = len(files["dwi_files"])
        subject_names += [subject_session_name] * n_images
        dwi_files += files["dwi_files"]
        bvecs_files += files["bvecs_files"]
        bvals_files += files["bvals_files"]
        if files["base"]:
            base_names += [files["base"]] * n_images
    dmrirc_list = ["setenv SUBJECTS_DIR {}".format(freesurfer_dir),
                   "set dtroot = {}".format(output_dir),
                   "set subjlist = ({})".format(" ".join(subject_names)),
                   "set dcmlist = ({})".format(" ".join(dwi_files)),
                   "set bveclist = ({})".format(" ".join(bvecs_files)),
                   "set bvallist = ({})".format(" ".join(bvals_files)),
                   ]

    if base_names:
        dmrirc_list.append("set baselist = ({})".format(" ".join(base_names)))

    dmrirc_str = "\n".join(dmrirc_list)
    dmrirc_file = os.path.join(output_dir, "sub-" + subject_label, "dmrirc")
    with open(dmrirc_file, "w") as fi:
        fi.write(dmrirc_str)
    return dmrirc_file


def run_trac_parallel(stage, jobs_dir, dmrirc_file, n_cpus, job_names=[""], sep="\n"):
    """
    use dmrirc file to create jobfiles for traclula bedpost or path and runs jobs in parallel via joblib
    stage: prep, bedp or path
    """

    jobs_filename = os.path.join(jobs_dir, stage + ".txt")

    # create job files
    cmd = "trac-all -{stage} -c {dmrirc_file} -jobs {jobs_filename}".format(stage=stage,
                                                                            dmrirc_file=dmrirc_file,
                                                                            jobs_filename=jobs_filename)
    run_cmd(cmd)

    # run jobs in parallel
    for j in job_names:
        job_file = os.path.join(jobs_dir, stage + j + ".txt")
        print("Running", job_file)
        with open(job_file) as fi:
            cmd_list = fi.read().strip().split(sep)
            cmd_list = [c for c in cmd_list if c]

        # create dirs before running bedpost
        if job_file.endswith("bedp.pre.txt"):
            for cmd in cmd_list:
                subject_dir = cmd.split(" ")[1]
                create_dirs = [os.path.join(subject_dir, "..", "dmri.bedpostX", "logs", "monitor"),
                               os.path.join(subject_dir, "..", "dmri.bedpostX", "xfms")]
                for d in create_dirs:
                    if not os.path.exists(d):
                        os.makedirs(d)

        # fix random seed
        if job_file.endswith("bedp.txt"):
            seed_str = " --seed=123"
            cmd_list = [c + seed_str for c in cmd_list]
            print(cmd_list)


        # for prep step: hack parallelization: tracula writes all prep commands (1/session + base) into one jobfile and
        # executes them sequentially; the session steps can be run in parallel, after they are finished, base needs
        # to be run
        # this works because we have one dmrirc file per subject
        if job_file.endswith("prep.txt"):
            base_cmd = cmd_list.pop(-1)

        print("Running commands", cmd_list)
        Parallel(n_jobs=n_cpus)(delayed(run_cmd)(cmd) for cmd in cmd_list)

        if job_file.endswith("prep.txt"):
            print("Running command", base_cmd)
            run_cmd(base_cmd)


def run_tract_all(dmrirc_file, output_dir, subject_label, stages, n_cpus):
    # run the processing steps prep, bedp and path
    subject_output_dir = os.path.join(output_dir, "sub-" + subject_label)
    jobs_dir = os.path.join(subject_output_dir, "jobs")
    if not os.path.exists(jobs_dir):
        os.makedirs(jobs_dir)

    if (("prep" in stages) or ("all" in stages)):
        stage = "prep"
        run_trac_parallel(stage, jobs_dir, dmrirc_file, n_cpus, job_names=[""], sep=";")

    if (("bedp" in stages) or ("all" in stages)):
        stage = "bedp"
        run_trac_parallel(stage, jobs_dir, dmrirc_file, n_cpus, job_names=[".pre", "", ".post"])

    if (("path" in stages) or ("all" in stages)):
        stage = "path"
        run_trac_parallel(stage, jobs_dir, dmrirc_file, n_cpus, job_names=[""])


def get_sessions(output_dir, subject_label):
    # returns sessions in tracula output dir for subject
    found_folders = sorted(glob(os.path.join(output_dir, "sub-{sub}*.long.*".format(sub=subject_label))))
    session_labels = []
    if found_folders:
        for f in found_folders:
            session_labels.append(os.path.basename(f).split(".")[0].split("_")[-1].split("-")[-1])
        return session_labels
    else:
        return None


def load_subject_motion_file(output_dir, subject_label, session_label=""):
    if session_label:
        long_str = "_ses-{ses}.long.sub-{sub}".format(ses=session_label, sub=subject_label)
    else:
        long_str = ""
    search_str = os.path.join(output_dir, "sub-" + subject_label + long_str, "dmri", "dwi_motion.txt")
    found_files = sorted(glob(search_str))
    assert len(found_files) < 2, "More than one motion file found, something is wrong. %s" % search_str
    if found_files:
        subject_motion_file = found_files[0]
        df_subject = pd.read_csv(subject_motion_file, sep=" ")
        df_subject.index = [subject_label]
        if session_label:
            df_subject["session_id"] = session_label
            # bring session id to the front
            c = df_subject.columns.tolist()
            c.remove("session_id")
            c = ["session_id"] + c
            df_subject = df_subject[c]
        return df_subject
    else:
        warn("Missing motion file for %s (%s). Skipping this subject." % (subject_label, search_str))
        return None


def get_subject_pathstats_file(output_dir, subject_label, tract, session_label=""):
    # returns pathstats filename for one tract for subject (and session, if longitudinal)
    if session_label:
        long_str = "_ses-{ses}.long.sub-{sub}".format(ses=session_label, sub=subject_label)
    else:
        long_str = ""

    search_str = os.path.join(output_dir, "sub-" + subject_label + long_str, "dpath",
                              tract + "*_avg33_mni_bbr/pathstats.overall.txt")
    found_files = sorted(glob(search_str))
    assert len(found_files) < 2, "More than one pathstats file found, something is wrong. %s" % search_str
    if found_files:
        subject_tract_stats_file = found_files[0]
        return subject_tract_stats_file
    else:
        warn("Missing file for %s (%s). Skipping this subject for this tract." % (
            subject_label, search_str))
        return None


def calculate_tmi(df):
    """
    calculate total motion index (TMI)
    according to Yendiki, A., Koldewyn, K., Kakunoori, S., Kanwisher, N., & Fischl, B. (2013).
    http://doi.org/10.1016/j.neuroimage.2013.11.027
    returns DataFrame
    """
    valid_metrics = []
    for m in ["AvgTranslation", "AvgRotation", "PercentBadSlices", "AvgDropoutScore"]:
        ql, med, qu = df[m].quantile(q=[.25, .5, .75])
        df[m + "_z"] = (df[m] - med) / (qu - ql)

        # since 'PercentBadSlices' and 'AvgDropoutScore' might show little variance (a majority of subjects with 0),
        #  which results in NaNs in standardized_m, only take other metrics
        if not df[m + "_z"].isnull().any():
            valid_metrics.append(m + "_z")
    df["TMI"] = df[valid_metrics].mean(1)
    df["TMI_info"] = "TMI based on " + ", ".join(valid_metrics)
    return df


def run_fs_if_not_available(layout, subject_label, freesurfer_dir, n_cpus, sessions=[]):
    freesurfer_subjects = []

    if len(sessions) > 1:
        # long
        for session_label in sessions:
            freesurfer_subjects.extend(["sub-{sub}".format(sub=subject_label),
                                        "sub-{sub}_ses-{ses}".format(sub=subject_label, ses=session_label),
                                        "sub-{sub}_ses-{ses}.long.sub-{sub}".format(sub=subject_label,
                                                                                    ses=session_label)])
    else:
        # cross
        freesurfer_subjects.extend(["sub-{sub}".format(sub=subject_label)])

    fs_missing = False
    for fss in freesurfer_subjects:
        if not os.path.exists(os.path.join(freesurfer_dir, fss, "scripts/recon-all.done")):
            fs_missing = True

    if fs_missing:
        print("FreeSurfer for {} not found. Running recon-all.".format(subject_label))

        # find T1w images for this subject
        t1w_files = [f.path for f in layout.get(subject=subject_label, datatype="anat", suffix="T1w",
                                                extension=[".nii", ".nii.gz"])]
        if not t1w_files:
            raise Exception("No T1w files found for subject %s" % subject_label)

        if len(sessions) > 1:
            # longitudinal: run cross-sectional per session, then template, then longitudinal
            for session_label in sessions:
                ses_t1w = [f.path for f in layout.get(subject=subject_label, session=session_label,
                                                      datatype="anat", suffix="T1w",
                                                      extension=[".nii", ".nii.gz"])]
                subject_id = "sub-{sub}_ses-{ses}".format(sub=subject_label, ses=session_label)
                if ses_t1w:
                    input_flags = " ".join(["-i " + f for f in ses_t1w])
                    cmd = "recon-all -s {sid} {inputs} -all -openmp {n_cpus}".format(
                        sid=subject_id, inputs=input_flags, n_cpus=n_cpus)
                    run_cmd(cmd, env={"SUBJECTS_DIR": freesurfer_dir})

            # create base template
            base_id = "sub-{sub}".format(sub=subject_label)
            tp_flags = " ".join(["-tp sub-{sub}_ses-{ses}".format(sub=subject_label, ses=s) for s in sessions])
            cmd = "recon-all -base {base} {tps} -all -openmp {n_cpus}".format(
                base=base_id, tps=tp_flags, n_cpus=n_cpus)
            run_cmd(cmd, env={"SUBJECTS_DIR": freesurfer_dir})

            # run longitudinal
            for session_label in sessions:
                subject_id = "sub-{sub}_ses-{ses}".format(sub=subject_label, ses=session_label)
                cmd = "recon-all -long {sid} {base} -all -openmp {n_cpus}".format(
                    sid=subject_id, base=base_id, n_cpus=n_cpus)
                run_cmd(cmd, env={"SUBJECTS_DIR": freesurfer_dir})
        else:
            # cross-sectional
            subject_id = "sub-{sub}".format(sub=subject_label)
            input_flags = " ".join(["-i " + f for f in t1w_files])
            cmd = "recon-all -s {sid} {inputs} -all -openmp {n_cpus}".format(
                sid=subject_id, inputs=input_flags, n_cpus=n_cpus)
            run_cmd(cmd, env={"SUBJECTS_DIR": freesurfer_dir})


def check_minimal_data_reqs(layout, subject_label, sessions_to_analyze):
    # check if minimal data requirements for subjects are satisfied (at least 1 t1w and 1 dwi image)
    # returns:
    #   valid_subject: boolean; if True subject has sufficient data to run traclula
    #   valid_sessions: list; for long data: list of sessions with sufficient data

    n_dwi = len(layout.get(subject=subject_label, datatype="dwi", suffix="dwi"))
    n_t1w = len(layout.get(subject=subject_label, datatype="anat", suffix="T1w"))
    if (n_dwi > 0) & (n_t1w > 0):
        valid_subject = True
    else:
        valid_subject = False

    # get sessions that have at least one dwi and one t1w image
    dwi_sessions = layout.get_sessions(subject=subject_label, datatype="dwi", suffix="dwi")
    t1w_sessions = layout.get_sessions(subject=subject_label, datatype="anat", suffix="T1w")
    sessions = list(set(dwi_sessions) & set(t1w_sessions))

    if sessions_to_analyze:
        sessions_not_found = list(set(sessions_to_analyze) - set(sessions))
        sessions = list(set(sessions) & set(sessions_to_analyze))
        if sessions_not_found:
            print("requested sessions %s not found for subject %s" % (" ".join(sessions_not_found), subject_label))
        if not sessions:
            valid_subject = False

    valid_sessions = []
    if valid_subject:
        for session in sessions:
            n_dwi = len(layout.get(subject=subject_label, session=session, datatype="dwi", suffix="dwi"))
            n_t1w = len(layout.get(subject=subject_label, session=session, datatype="anat", suffix="T1w"))
            if (n_dwi > 0) & (n_t1w > 0):
                valid_sessions.append(session)

        # for cases that have zero sessions with both (t1w and dwi) data:
        if len(valid_sessions) == 0:
            if sessions_to_analyze:
                valid_subject = False

    return valid_subject, valid_sessions


def participant_level(args, layout, subjects_to_analyze, sessions_to_analyze):
    # if only one session is available for the entire study, use cross sectional stream
    truly_longitudinal_study = True if len(layout.get_sessions()) > 1 else False

    for subject_label in subjects_to_analyze:
        subject_session_info = OrderedDict()
        valid_subject, valid_sessions = check_minimal_data_reqs(layout, subject_label, sessions_to_analyze)

        if valid_subject:
            # check for freesurfer and run if missing
            run_fs_if_not_available(layout, subject_label, args.freesurfer_dir, args.n_cpus, valid_sessions)

            # run full tracula processing
            if valid_sessions and truly_longitudinal_study:
                # long
                for session_label in valid_sessions:
                    dwi_files, bvecs_files, bvals_files = get_data(layout, subject_label,
                                                                   args.freesurfer_dir,
                                                                   truly_longitudinal_study,
                                                                   session_label=session_label)

                    subject_session_name = "sub-" + subject_label + "_ses-" + session_label
                    subject_session_info[subject_session_name] = {"dwi_files": dwi_files,
                                                                  "bvecs_files": bvecs_files,
                                                                  "bvals_files": bvals_files,
                                                                  "base": "sub-" + subject_label}

            else:
                # cross
                subject_session_name = "sub-" + subject_label
                dwi_files, bvecs_files, bvals_files = get_data(layout,
                                                               subject_label,
                                                               args.freesurfer_dir,
                                                               truly_longitudinal_study)
                subject_session_info[subject_session_name] = {"dwi_files": dwi_files,
                                                              "bvecs_files": bvecs_files,
                                                              "bvals_files": bvals_files,
                                                              "base": ""}

            if subject_session_info:
                subject_output_dir = os.path.join(args.output_dir, "sub-" + subject_label)
                if not os.path.exists(subject_output_dir):
                    os.makedirs(subject_output_dir)

                # create dmrirc file and run trac-all commands
                dmrirc_file = create_dmrirc(args.freesurfer_dir, args.output_dir, subject_label,
                                            subject_session_info)
                run_tract_all(dmrirc_file, args.output_dir, subject_label, args.stages, args.n_cpus)
        else:
            warn("Subject {} has not enough data to run TRACULA".format(subject_label))


def group_level_motion_stats(args, subjects_to_analyze):
    # collect motion stats
    motion_output_dir = os.path.join(args.output_dir, "00_group1_motion_stats")
    if not os.path.exists(motion_output_dir):
        os.makedirs(motion_output_dir)
    motion_output_file = os.path.join(motion_output_dir, "group_motion.tsv")
    dfs = []
    for subject_label in subjects_to_analyze:
        sessions = get_sessions(args.output_dir, subject_label)

        if sessions:
            for session_label in sessions:
                df_subject = load_subject_motion_file(args.output_dir, subject_label, session_label)
                if df_subject is not None:
                    dfs.append(df_subject)
        else:
            df_subject = load_subject_motion_file(args.output_dir, subject_label, session_label="")
            if df_subject is not None:
                dfs.append(df_subject)
    df = pd.concat(dfs) if dfs else pd.DataFrame()
    df = calculate_tmi(df)
    df.index.name = "participant_id"
    df.to_csv(motion_output_file, sep="\t")


def group_level_tract_pathstats(args, subjects_to_analyze):
    # run overall stats
    group_output_dir = os.path.join(args.output_dir, "00_group2_tract_stats")
    overall_stats_output_dir = os.path.join(group_output_dir, "overall_stats")
    tract_file_list_dir = os.path.join(overall_stats_output_dir, "00_file_lists")
    if not os.path.exists(tract_file_list_dir):
        os.makedirs(tract_file_list_dir)
    hemis = ["lh", "rh"]
    tracts = ["fmajor", "fminor"] + [h + "." + t for h, t in
                                     (product(hemis, ["cst", "unc", "ilf", "atr", "ccg", "cab", "slfp", "slft"]))]
    for tract in tracts:
        tract_file_list = []
        tract_file_list_output_file = os.path.join(tract_file_list_dir, tract + "_list.txt")

        for subject_label in subjects_to_analyze:
            sessions = get_sessions(args.output_dir, subject_label)

            if sessions:
                # long
                for session_label in sessions:
                    subject_tract_stats_file = get_subject_pathstats_file(args.output_dir, subject_label, tract,
                                                                          session_label=session_label)
                    if subject_tract_stats_file:
                        tract_file_list.append(subject_tract_stats_file)

            else:
                # cross
                subject_tract_stats_file = get_subject_pathstats_file(args.output_dir, subject_label, tract,
                                                                      session_label="")
                if subject_tract_stats_file:
                    tract_file_list.append(subject_tract_stats_file)

        with open(tract_file_list_output_file, "w") as fi:
            fi.write("\n".join(tract_file_list))

        tract_stats_file = os.path.join(overall_stats_output_dir, tract + "_stats.tsv")
        cmd = "tractstats2table --load-pathstats-from-file {} --overall --tablefile {}".format(
            tract_file_list_output_file, tract_stats_file)
        run_cmd(cmd)

        # reformat tract stats file
        df = pd.read_csv(tract_stats_file, sep="\t")
        df["tract"] = tract
        df.rename(columns={tract: "participant_id"}, inplace=True)
        df.to_csv(tract_stats_file, sep="\t", index=False)

    # create byvoxel stats
    dmrirc_file = os.path.join(overall_stats_output_dir, "dmrirc_groupstats")
    subjects = " ".join(df["participant_id"].tolist())
    dmrirc_str = """
    set dtroot = {}
    set subjlist = ( {} )
    """.format(args.output_dir, subjects)
    with open(dmrirc_file, "w") as fi:
        fi.write(dmrirc_str)

    cmd = "trac-all -stat -c {}".format(dmrirc_file)
    run_cmd(cmd)

    # creates a folder in outdir; move folder
    stats_source_folder = os.path.join(args.output_dir, "stats")
    stats_dest_folder = os.path.join(group_output_dir, "byvoxel_stats")
    shutil.move(stats_source_folder, stats_dest_folder)
