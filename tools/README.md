# Tools Setup

**Prerequisites:** Java JDK 8+, Maven (for building DesigniteJava from source)

## DesigniteJava (v1.1.2)
Detects design/implementation smells and computes OO metrics.

Install: `cd tools/DesigniteJava-src && mvn clean package -DskipTests && cp target/DesigniteJava.jar ../DesigniteJava.jar`
```bash
java -jar tools/DesigniteJava.jar -i <source-path> -o <output-dir>
```
Build from source: `cd tools/DesigniteJava-src && mvn clean package`

## RefactoringMiner (v3.0.10)
Detects refactorings between Git commits.

Install: `chmod +x tools/RefactoringMiner-3.0.10/bin/RefactoringMiner` (pre-built, no build needed)
```bash
chmod +x tools/RefactoringMiner-3.0.10/bin/RefactoringMiner  # first time only
tools/RefactoringMiner-3.0.10/bin/RefactoringMiner -c <repo-path> <commit-sha>
tools/RefactoringMiner-3.0.10/bin/RefactoringMiner -a <repo-path>
```

## EvoSuite (v1.2.0)
Auto-generates JUnit tests. **Requires Java 8.**

Install: `wget https://github.com/EvoSuite/evosuite/releases/download/v1.2.0/evosuite-1.2.0.jar -O tools/evosuite-1.2.0.jar` (or use the included JAR)
```bash
java -jar tools/evosuite-1.2.0.jar -class <ClassName> -projectCP <classpath>
java -jar tools/evosuite-1.2.0.jar -target <project.jar>
```
Output: `evosuite-tests/` and `evosuite-report/`
