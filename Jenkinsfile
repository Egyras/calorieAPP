pipeline {
    agent any

    environment {
        IMAGE        = 'vaikis/calorie-tracker'
        TAG          = 'latest'
    }

    triggers {
        pollSCM('* * * * *')
    }

    stages {

        stage('Checkout') {
            steps {
                checkout scm
                echo "Building commit ${env.GIT_COMMIT?.take(8)} on ${env.GIT_BRANCH}"
            }
        }

        stage('Build image') {
            steps {
                sh """
                    docker build \
                        -t ${IMAGE}:${TAG} \
                        -t ${IMAGE}:build-${env.BUILD_NUMBER} \
                        .
                """
            }
        }

        stage('Push to Docker Hub') {
            steps {
                withCredentials([usernamePassword(
                    credentialsId: 'dockerhub',
                    usernameVariable: 'DOCKER_USER',
                    passwordVariable: 'DOCKER_PASS'
                )]) {
                    sh """
                        echo "\$DOCKER_PASS" | docker login -u "\$DOCKER_USER" --password-stdin
                        docker push ${IMAGE}:${TAG}
                        docker push ${IMAGE}:build-${env.BUILD_NUMBER}
                        docker logout
                    """
                }
            }
        }

        stage('Deploy on TrueNAS') {
            steps {
                withCredentials([
                    string(credentialsId: 'calorie-google-client-id',     variable: 'GOOGLE_CLIENT_ID'),
                    string(credentialsId: 'calorie-google-client-secret', variable: 'GOOGLE_CLIENT_SECRET'),
                    string(credentialsId: 'calorie-secret-key',           variable: 'SECRET_KEY'),
                    string(credentialsId: 'calorie-allowed-emails',       variable: 'ALLOWED_EMAILS')
                ]) {
                    sh """
                        docker pull ${IMAGE}:${TAG}

                        docker stop calorie-tracker || true
                        docker rm   calorie-tracker || true

                        OLDPORT=\$(docker ps -q --filter publish=5555)
                        if [ -n "\$OLDPORT" ]; then
                            docker stop \$OLDPORT || true
                            docker rm   \$OLDPORT || true
                        fi

                        docker run -d \
                            --name    calorie-tracker \
                            --restart unless-stopped \
                            -p        5555:8080 \
                            -e        DB_PATH=/data/calories.db \
                            -e        SECRET_KEY="\${SECRET_KEY}" \
                            -e        GOOGLE_CLIENT_ID="\${GOOGLE_CLIENT_ID}" \
                            -e        GOOGLE_CLIENT_SECRET="\${GOOGLE_CLIENT_SECRET}" \
                            -e        ALLOWED_EMAILS="\${ALLOWED_EMAILS}" \
                            -v        calorie-tracker-data:/data \
                            ${IMAGE}:${TAG}

                        echo "Deployed ${IMAGE}:${TAG} as build #${env.BUILD_NUMBER}"
                    """
                }
            }
        }
    }

    post {
        always {
            sh 'docker image prune -f --filter "dangling=true" || true'
        }
        success {
            echo "✅ Build #${env.BUILD_NUMBER} deployed — http://192.168.8.211:5555"
        }
        failure {
            echo "❌ Build #${env.BUILD_NUMBER} failed"
        }
    }
}
