// VeriSpec pipeline (Jenkins) — CLI equivalent of the GitHub composite Action.
//
// Required job parameter / env: VERISPEC_SPEC
// Optional: VERISPEC_TRACES (comma-separated), VERISPEC_BOUND (default 10)

pipeline {
  agent any
  environment {
    VERISPEC_BOUND = "${env.VERISPEC_BOUND ?: '10'}"
    VERISPEC_TIMEOUT_MS = "${env.VERISPEC_TIMEOUT_MS ?: '30000'}"
  }
  stages {
    stage('Install VeriSpec') {
      steps {
        sh '''
          python3 -m pip install --upgrade pip
          python3 -m pip install "verispec @ git+https://github.com/verispec/verispec.git@main"
          verispec version
        '''
      }
    }
    stage('Verify contract') {
      steps {
        sh '''
          set -euo pipefail
          test -n "${VERISPEC_SPEC:?set VERISPEC_SPEC to your .verispec.yaml}"
          verispec validate "$VERISPEC_SPEC"
          verispec lint "$VERISPEC_SPEC"
          verispec check "$VERISPEC_SPEC" --bound "$VERISPEC_BOUND" --timeout "$VERISPEC_TIMEOUT_MS"
          if [ -n "${VERISPEC_TRACES:-}" ]; then
            IFS=',' read -ra traces <<< "$VERISPEC_TRACES"
            for t in "${traces[@]}"; do
              t="$(echo "$t" | tr -d '[:space:]')"
              [ -z "$t" ] && continue
              verispec check-trace "$VERISPEC_SPEC" "$t"
            done
          fi
          verispec report "$VERISPEC_SPEC" --bound "$VERISPEC_BOUND" -o verispec-report.html
        '''
      }
    }
  }
  post {
    always {
      archiveArtifacts artifacts: 'verispec-report.html', allowEmptyArchive: true
    }
  }
}
