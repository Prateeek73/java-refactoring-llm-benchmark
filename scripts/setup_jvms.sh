#!/usr/bin/env bash
# setup_jvms.sh — Install all required JDKs + Maven for the EvoSuite pipeline.
# Usage: sudo bash scripts/setup_jvms.sh
set -euo pipefail

echo "=== Installing JDK 8, 11, 17, 21 ==="
apt-get update -qq
apt-get install -y --no-install-recommends \
    openjdk-8-jdk \
    openjdk-11-jdk \
    openjdk-17-jdk \
    openjdk-21-jdk

echo ""
echo "=== Verifying JDK installations ==="
for v in 8 11 17 21; do
    jpath="/usr/lib/jvm/java-${v}-openjdk-amd64"
    if [ -x "${jpath}/bin/java" ]; then
        ver=$("${jpath}/bin/java" -version 2>&1 | head -1)
        echo "  JDK ${v}: OK  (${ver})"
    else
        echo "  JDK ${v}: MISSING at ${jpath}"
    fi
done

echo ""
echo "=== Installing Maven 3.9.9 ==="
MVN_VER="3.9.9"
MVN_URL="https://downloads.apache.org/maven/maven-3/${MVN_VER}/binaries/apache-maven-${MVN_VER}-bin.tar.gz"

if [ -x "/opt/maven/bin/mvn" ]; then
    echo "  Maven already installed at /opt/maven"
else
    echo "  Downloading Maven ${MVN_VER}..."
    curl -fsSL "${MVN_URL}" -o /tmp/maven.tar.gz
    mkdir -p /opt/maven
    tar -xzf /tmp/maven.tar.gz -C /opt/maven --strip-components=1
    rm -f /tmp/maven.tar.gz
    ln -sf /opt/maven/bin/mvn /usr/local/bin/mvn
    echo "  Maven installed to /opt/maven"
fi

echo ""
echo "=== Verifying Maven ==="
/opt/maven/bin/mvn --version

echo ""
echo "=== All done ==="
echo "JDKs: /usr/lib/jvm/java-{8,11,17,21}-openjdk-amd64"
echo "Maven: /opt/maven/bin/mvn (also /usr/local/bin/mvn)"
