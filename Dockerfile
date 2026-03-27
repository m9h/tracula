# TRACULA BIDS App — built on Neurodesk containers
# https://github.com/NeuroDesk/neurocontainers

# Stage 1: FSL binaries
FROM ghcr.io/neurodesk/fsl:6.0.7.19 AS fsl-stage

# Stage 2: FreeSurfer base + FSL + BIDS App layer
FROM ghcr.io/neurodesk/freesurfer:8.0.0

ARG DEBIAN_FRONTEND="noninteractive"

# Copy FSL from fsl-stage
COPY --from=fsl-stage /opt/fsl-6.0.7.19 /opt/fsl-6.0.7.19

# FSL environment
ENV FSLDIR=/opt/fsl-6.0.7.19
ENV FSLOUTPUTTYPE=NIFTI_GZ
ENV FSLMULTIFILEQUIT=TRUE
ENV FSLTCLSH=/usr/bin/tclsh
ENV FSLWISH=/usr/bin/wish
ENV PATH=${FSLDIR}/bin:${PATH}
ENV LD_LIBRARY_PATH=${FSLDIR}/lib:${LD_LIBRARY_PATH}

# Python dependencies
RUN apt-get update -qq && \
    apt-get install -y -q --no-install-recommends \
        python3-pip && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt

# BIDS App code
RUN mkdir -p /code
COPY run.py /code/run.py
COPY tracula.py /code/tracula.py
RUN chmod +x /code/run.py

COPY version /version
ENV PATH=/code:${PATH}

# FreeSurfer license must be mounted at runtime:
#   docker run -v /path/to/license.txt:$FREESURFER_HOME/license.txt ...

ENTRYPOINT ["/code/run.py"]
