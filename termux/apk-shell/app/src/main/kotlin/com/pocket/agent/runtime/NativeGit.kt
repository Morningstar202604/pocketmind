package com.pocket.agent.runtime

import org.eclipse.jgit.api.Git
import org.eclipse.jgit.api.MergeCommand
import org.eclipse.jgit.api.ResetCommand
import org.eclipse.jgit.diff.DiffAlgorithm
import org.eclipse.jgit.diff.Edit
import org.eclipse.jgit.diff.HistogramDiff
import org.eclipse.jgit.diff.RawText
import org.eclipse.jgit.diff.RawTextComparator
import org.eclipse.jgit.dircache.DirCache
import org.eclipse.jgit.lib.BranchTrackingStatus
import org.eclipse.jgit.lib.CommitBuilder
import org.eclipse.jgit.lib.Constants
import org.eclipse.jgit.lib.ObjectId
import org.eclipse.jgit.lib.PersonIdent
import org.eclipse.jgit.lib.RefUpdate
import org.eclipse.jgit.lib.Repository
import org.eclipse.jgit.lib.RepositoryState
import org.eclipse.jgit.revwalk.RevWalk
import org.eclipse.jgit.treewalk.TreeWalk
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.nio.charset.StandardCharsets

/**
 * 原生 Git：JGit 纯 Java 实现，不依赖 Termux 里的 git 二进制。
 *
 * 只做工作区需要的子集：状态、分支切换、预合并/提交/中止/撤销、冲突读取与整文件解决。
 * 三路块（diff3）由 Python 运行时提供；原生侧显式返回 git_blocks=false，UI 不显示该按钮。
 * 所有路径先 canonical 化，仓库根必须仍在应用 filesDir 内，防止被 .git 指到外部目录。
 */
class NativeGit(private val root: File) {
    private val rootPath = root.canonicalPath

    fun init(cwd: File): JSONObject {
        val dir = checkedDir(cwd)
        if (openOrNull(dir) != null) {
            // 已有仓库：直接返回状态，重复 init 不报错
            return status(dir)
        }
        Git.init().setDirectory(dir).setInitialBranch("main").call().use { g ->
            val repo = g.repository
            val cfg = repo.config
            if (cfg.getString("user", null, "name").isNullOrBlank()) {
                cfg.setString("user", null, "name", "PocketMind")
                cfg.setString("user", null, "email", "pocketmind@local")
                cfg.save()
            }
            val ignore = File(dir, ".gitignore")
            if (!ignore.exists()) {
                ignore.writeText(
                    "config.json\nrules.json\nrules.json.bak\npocketmind.db*\n" +
                        "workspace_changes.json\nworkspace_changes.json.tmp\n" +
                        ".workspace-backups/\nworkspace_tasks/\ncheckpoints/\naudit/\n*.json.tmp\n",
                    Charsets.UTF_8,
                )
            }
            g.add().addFilepattern(".gitignore").call()
            g.commit().setMessage("chore: initialize PocketMind workspace")
                .setAuthor("PocketMind", "pocketmind@local")
                .setCommitter("PocketMind", "pocketmind@local").call()
        }
        return status(dir).put("initialized", true)
    }

    /** 克隆公开 HTTPS 仓库到工作区子目录；目标必须不存在或为空。 */
    fun clone(url: String, target: String, branch: String? = null): JSONObject {
        val uri = url.trim()
        if (!uri.startsWith("https://") && !uri.startsWith("http://") && !uri.startsWith("file:")) {
            fail(400, "原生 Git 支持 http(s) 与 file:// 仓库地址；私有库请把 token 放进 URL")
        }
        val rel = normalizeRel(target)
        val dir = File(root, rel).canonicalFile
        if (dir != root && !dir.path.startsWith(rootPath + File.separator)) fail(403, "目标路径越界")
        if (dir.exists() && (dir.listFiles()?.isNotEmpty() == true)) fail(409, "目标目录非空：$rel")
        dir.parentFile?.mkdirs()
        val cmd = Git.cloneRepository()
            .setURI(uri)
            .setDirectory(dir)
            .setCloneAllBranches(true)
        if (!branch.isNullOrBlank()) cmd.setBranch(validateBranch(branch))
        try {
            cmd.call().use { }
        } catch (e: Exception) {
            runCatching { dir.deleteRecursively() }
            fail(400, "克隆失败：${e.message}")
        }
        return status(dir).put("cloned", rel).put("url", uri)
    }

    /** 从 origin 拉取当前分支；有冲突时返回 merging 状态而不是抛异常。 */
    fun pull(cwd: File): JSONObject {
        val dir = checkedDir(cwd)
        val git = open(dir)
        git.use {
            try {
                it.fetch().setRemote("origin").setRemoveDeletedRefs(true).call()
            } catch (e: Exception) {
                fail(400, "fetch 失败：${e.message}")
            }
            val pull = try {
                it.pull().setRemote("origin").call()
            } catch (e: Exception) {
                // JGit 在冲突时可能抛出 CheckoutConflictException；保留工作区现场，交给冲突面板。
                val st = status(dir)
                if (st.optBoolean("merging") || !st.optBoolean("clean")) {
                    return JSONObject().put("ok", false).put("message", e.message ?: "pull 有冲突")
                        .put("state", st)
                }
                fail(400, "pull 失败：${e.message}")
            }
            val merged = pull.mergeResult?.mergeStatus?.toString() ?: "FAST_FORWARD"
            return JSONObject().put("ok", true).put("merge_status", merged).put("state", status(dir))
        }
    }

    fun status(cwd: File): JSONObject {
        val dir = checkedDir(cwd)
        val git = openOrNull(dir)
            ?: return JSONObject().put("available", false).put("cwd", dir.absolutePath)
                .put("repo_root", "").put("message", "当前目录不是 Git 仓库；点“初始化仓库”或把项目放进工作区。")
        return git.use {
            val repo = it.repository
            val st = it.status().call()
            val headRef = repo.exactRef(Constants.HEAD)
            val branch = repo.branch ?: ""
            val detached = headRef != null && !headRef.isSymbolic
            val head = repo.resolve(Constants.HEAD)
            val merging = repo.repositoryState == RepositoryState.MERGING ||
                repo.repositoryState == RepositoryState.MERGING_RESOLVED
            val mergeHeads = repo.readMergeHeads().orEmpty()
            val tracking = if (branch.isNotEmpty()) {
                try { BranchTrackingStatus.of(repo, branch) } catch (_: Exception) { null }
            } else null
            val files = fileEntries(st)
            val branches = JSONArray()
            for (ref in it.branchList().call()) {
                val name = Repository.shortenRefName(ref.name)
                val b = JSONObject().put("name", name)
                    .put("sha", ref.objectId?.name ?: "")
                    .put("upstream", trackingOf(repo, name))
                    .put("current", name == branch)
                branches.put(b)
            }
            val conflicts = st.conflicting.toList().sorted()
            JSONObject()
                .put("available", true)
                .put("cwd", dir.absolutePath)
                .put("repo_root", repo.workTree.canonicalPath)
                .put("branch", branch)
                .put("detached", detached)
                .put("commit", head?.name?.take(7) ?: "")
                .put("upstream", tracking?.remoteTrackingBranch ?: "")
                .put("ahead", tracking?.aheadCount)
                .put("behind", tracking?.behindCount)
                .put("clean", st.isClean && conflicts.isEmpty())
                .put("merging", merging)
                .put("merge_head", mergeHeads.firstOrNull()?.name?.take(7) ?: "")
                .put("files", files)
                .put("branches", branches)
                .put("conflicts", JSONArray(conflicts))
                .put("diffstat", "")
                .put("message", if (merging) "合并进行中" else "")
        }
    }

    fun checkout(cwd: File, branch: String, create: Boolean): JSONObject {
        val name = validateBranch(branch)
        val dir = checkedDir(cwd)
        val git = open(dir)
        git.use {
            val repo = it.repository
            if (repo.repositoryState.isMergingState()) fail(409, "合并进行中，先提交或中止合并")
            val st = it.status().call()
            if (!st.isClean) fail(409, "工作区有未提交改动，先提交或 stash 再切换分支")
            val exists = repo.findRef("refs/heads/$name") != null
            if (!exists && !create) fail(404, "分支不存在：$name")
            if (exists && create) fail(409, "分支已存在：$name")
            it.checkout().setName(name).setCreateBranch(create).call()
            return JSONObject().put("ok", true).put("branch", name).put("state", status(dir))
        }
    }

    fun commitAll(cwd: File, message: String): JSONObject {
        val dir = checkedDir(cwd)
        val git = open(dir)
        git.use {
            val repo = it.repository
            if (repo.repositoryState.isMergingState()) fail(409, "合并进行中，请用“提交合并”")
            val msg = message.trim()
            if (msg.isEmpty()) fail(400, "提交说明不能为空")
            if (msg.length > 500) fail(400, "提交说明过长")
            it.add().addFilepattern(".").call()
            val st = it.status().call()
            val staged = st.added.isNotEmpty() || st.changed.isNotEmpty() ||
                st.removed.isNotEmpty() || st.conflicting.isNotEmpty()
            if (!staged) fail(409, "没有可提交的改动")
            val commit = it.commit().setMessage(msg).call()
            return JSONObject().put("ok", true)
                .put("commit", commit.name.take(7))
                .put("state", status(dir))
        }
    }

    fun mergePreview(cwd: File, branch: String): JSONObject {
        val name = validateBranch(branch)
        val dir = checkedDir(cwd)
        val git = open(dir)
        git.use {
            val repo = it.repository
            val current = repo.branch ?: ""
            if (name == current) fail(400, "不能把分支合并到它自己")
            if (repo.repositoryState.isMergingState()) fail(409, "已有未完成的合并，先提交或中止")
            val st = it.status().call()
            if (!st.isClean) fail(409, "工作区有未提交改动，先提交或 stash 后再合并")
            val target = repo.findRef("refs/heads/$name") ?: fail(404, "分支不存在：$name")
            val cp = createCheckpoint(repo)
            val result = it.merge()
                .include(target)
                .setCommit(false)
                .setFastForward(MergeCommand.FastForwardMode.NO_FF)
                .setMessage("Merge branch '$name'")
                .call()
            val state = status(dir)
            return JSONObject().put("ok", result.mergeStatus != null)
                .put("checkpoint", cp)
                .put("merge_exit", if (result.mergeStatus == null) 1 else 0)
                .put("branch", name)
                .put("clean", state.optJSONArray("conflicts")?.length() == 0)
                .put("conflicts", state.optJSONArray("conflicts") ?: JSONArray())
                .put("stdout", "")
                .put("stderr", result.toString())
                .put("state", state)
        }
    }

    fun mergeAbort(cwd: File): JSONObject {
        val dir = checkedDir(cwd)
        val git = open(dir)
        git.use {
            val repo = it.repository
            if (!repo.repositoryState.isMergingState()) fail(409, "当前没有进行中的合并")
            val cp = readCheckpoint(repo)
            it.reset().setMode(ResetCommand.ResetType.HARD).setRef(Constants.HEAD).call()
            clearMergeFiles(repo)
            clearCheckpoint(repo)
            return JSONObject().put("ok", true).put("checkpoint", cp ?: JSONObject.NULL).put("state", status(dir))
        }
    }

    fun mergeCommit(cwd: File, message: String): JSONObject {
        val dir = checkedDir(cwd)
        val git = open(dir)
        git.use {
            val repo = it.repository
            if (!repo.repositoryState.isMergingState()) fail(409, "当前没有进行中的合并")
            val st = it.status().call()
            if (st.conflicting.isNotEmpty()) fail(409, "还有未解决的冲突文件")
            val heads = repo.readMergeHeads().orEmpty()
            if (heads.isEmpty()) fail(409, "缺少 MERGE_HEAD")
            val msg = message.trim().ifEmpty { "Merge branch" }.take(500)
            val cp = readCheckpoint(repo)
            val index = DirCache.read(repo)
            val inserter = repo.newObjectInserter()
            val treeId = try { index.writeTree(inserter) } catch (e: Exception) {
                fail(409, "索引中仍有未合并文件：${e.message}")
            }
            val headId = repo.resolve(Constants.HEAD) ?: fail(409, "HEAD 无效")
            val ident = PersonIdent(repo)
            val builder = CommitBuilder()
            builder.setTreeId(treeId)
            builder.setParentIds(headId, heads[0])
            builder.setAuthor(ident)
            builder.setCommitter(ident)
            builder.setMessage(msg)
            val commit = inserter.insert(builder)
            inserter.flush()
            val headRef = repo.exactRef(Constants.HEAD)
            val target = if (headRef?.isSymbolic == true) headRef.target?.name else Constants.HEAD
            val ru = repo.updateRef(target ?: Constants.HEAD)
            ru.isForceUpdate = true
            ru.setNewObjectId(commit)
            val result = ru.update()
            if (result == RefUpdate.Result.REJECTED || result == RefUpdate.Result.LOCK_FAILURE ||
                result == RefUpdate.Result.IO_FAILURE) {
                fail(409, "更新 HEAD 失败：$result")
            }
            clearMergeFiles(repo)
            return JSONObject().put("ok", true)
                .put("output", "commit ${commit.name.take(7)}")
                .put("checkpoint", cp ?: JSONObject.NULL)
                .put("state", status(dir))
        }
    }

    fun undoMerge(cwd: File): JSONObject {
        val dir = checkedDir(cwd)
        val git = open(dir)
        git.use {
            val repo = it.repository
            val cp = readCheckpoint(repo) ?: fail(409, "没有可撤销的合并检查点")
            val st = it.status().call()
            if (!st.isClean) fail(409, "工作区有未提交改动，先提交/stash 再撤销合并")
            val head = repo.resolve(Constants.HEAD) ?: fail(409, "HEAD 无效")
            RevWalk(repo).use { rw ->
                val c = rw.parseCommit(head)
                if (c.parentCount < 2) fail(409, "当前 HEAD 不是合并提交，拒绝 reset")
            }
            val sha = ObjectId.fromString(cp.getString("sha"))
            it.reset().setMode(ResetCommand.ResetType.HARD).setRef(sha.name).call()
            clearCheckpoint(repo)
            return JSONObject().put("ok", true).put("checkpoint", cp).put("state", status(dir))
        }
    }

    fun checkpoint(cwd: File): JSONObject {
        val dir = checkedDir(cwd)
        val git = open(dir)
        git.use {
            return JSONObject().put("ok", true).put("checkpoint", readCheckpoint(it.repository) ?: JSONObject.NULL)
        }
    }

    fun conflicts(cwd: File): JSONObject {
        val dir = checkedDir(cwd)
        val git = open(dir)
        git.use {
            val repo = it.repository
            val paths = it.status().call().conflicting.toList().sorted()
            val items = JSONArray()
            for (rel in paths) {
                val path = safeRepoPath(repo, rel)
                val stages = stageTexts(repo, rel)
                val binary = stages.values.any { it.first }
                val work = readWorkText(path)
                items.put(JSONObject()
                    .put("path", rel)
                    .put("binary", binary || work.first)
                    .put("base", if (binary) "" else stages["base"]?.second ?: "")
                    .put("ours", if (binary) "" else stages["ours"]?.second ?: "")
                    .put("theirs", if (binary) "" else stages["theirs"]?.second ?: "")
                    .put("work", if (binary) "" else work.second))
            }
            return JSONObject().put("items", items).put("count", items.length())
        }
    }

    fun conflictBlocks(cwd: File, rawPath: String): JSONObject {
        val dir = checkedDir(cwd)
        val git = open(dir)
        git.use {
            val repo = it.repository
            val rel = normalizeRel(rawPath)
            safeRepoPath(repo, rel)
            if (rel !in it.status().call().conflicting) fail(409, "该文件当前不是冲突文件")
            val stages = stageTexts(repo, rel)
            val binary = stages.values.any { it.first }
            if (binary) return JSONObject().put("path", rel).put("binary", true)
                .put("blocks", JSONArray()).put("count", 0)
            val computed = threeWay(
                stages["base"]?.second ?: "",
                stages["ours"]?.second ?: "",
                stages["theirs"]?.second ?: "",
            )
            return JSONObject().put("path", rel).put("binary", false)
                .put("blocks", JSONArray(computed.blocks.map { b ->
                    JSONObject().put("index", b.index).put("ours", b.ours)
                        .put("base", b.base).put("theirs", b.theirs)
                })).put("count", computed.blocks.size)
        }
    }

    fun resolveConflictBlocks(cwd: File, rawPath: String, choices: JSONArray): JSONObject {
        val dir = checkedDir(cwd)
        val git = open(dir)
        git.use {
            val repo = it.repository
            val rel = normalizeRel(rawPath)
            val target = safeRepoPath(repo, rel)
            if (rel !in it.status().call().conflicting) fail(409, "该文件当前不是冲突文件")
            val stages = stageTexts(repo, rel)
            if (stages.values.any { it.first }) fail(409, "二进制冲突不能按块解决")
            val computed = threeWay(
                stages["base"]?.second ?: "",
                stages["ours"]?.second ?: "",
                stages["theirs"]?.second ?: "",
            )
            if (computed.blocks.isEmpty()) fail(409, "没有可解决的冲突块，请用整文件解决")
            data class Pick(val side: String, val content: String?)
            val picks = LinkedHashMap<Int, Pick>()
            for (i in 0 until choices.length()) {
                val o = choices.optJSONObject(i) ?: fail(400, "choices 格式不合法")
                val idx = o.optInt("index", -1)
                if (idx < 0 || idx >= computed.blocks.size || idx in picks) fail(400, "冲突块序号不合法或重复：$idx")
                val side = o.optString("side", "ours")
                if (side !in setOf("ours", "theirs", "base", "manual")) fail(400, "side 只能是 ours / theirs / base / manual")
                picks[idx] = Pick(side, if (o.has("content") && !o.isNull("content")) o.optString("content") else null)
            }
            if (picks.size != computed.blocks.size) {
                val missing = computed.blocks.map { it.index }.filter { it !in picks }.take(6)
                fail(400, "还有未选择的冲突块：$missing")
            }
            val out = StringBuilder()
            for (seg in computed.segments) {
                if (seg.block == null) { out.append(seg.text); continue }
                val pick = picks[seg.block.index]!!
                out.append(when (pick.side) {
                    "manual" -> pick.content.orEmpty()
                    else -> seg.block.valueOf(pick.side)
                })
            }
            var finalText = out.toString()
            val wantsNewline = stages.values.any { it.second.endsWith("\n") }
            if (wantsNewline && finalText.isNotEmpty() && !finalText.endsWith("\n")) finalText += "\n"
            if (finalText.length > 800_000) fail(413, "解决后的文件过大")
            if (java.nio.file.Files.isSymbolicLink(target.toPath())) fail(409, "拒绝写入符号链接冲突文件")
            target.parentFile?.mkdirs()
            target.writeText(finalText, Charsets.UTF_8)
            it.add().addFilepattern(rel).call()
            return JSONObject().put("ok", true).put("path", rel)
                .put("remaining", JSONArray(it.status().call().conflicting.toList().sorted()))
                .put("state", status(dir))
        }
    }

    fun resolveConflict(cwd: File, rawPath: String, strategy: String, content: String?): JSONObject {
        val dir = checkedDir(cwd)
        val git = open(dir)
        git.use {
            val repo = it.repository
            val rel = normalizeRel(rawPath)
            val path = safeRepoPath(repo, rel)
            if (rel !in it.status().call().conflicting) fail(409, "该文件当前不是冲突文件")
            val side = strategy.trim().ifEmpty { "ours" }
            when (side) {
                "ours", "theirs" -> {
                    val stage = if (side == "ours") 2 else 3
                    val blob = stageId(repo, rel, stage)
                    if (blob == null) {
                        if (!path.delete()) fail(409, "取 $side 版本失败：文件不存在")
                    } else {
                        val bytes = repo.newObjectReader().use { r -> r.open(blob).bytes }
                        path.parentFile?.mkdirs()
                        path.writeBytes(bytes)
                    }
                }
                "manual" -> {
                    if (java.nio.file.Files.isSymbolicLink(path.toPath())) fail(409, "拒绝写入符号链接冲突文件")
                    path.parentFile?.mkdirs()
                    path.writeText(content.orEmpty(), Charsets.UTF_8)
                }
                else -> fail(400, "strategy 只能是 ours / theirs / manual")
            }
            it.add().addFilepattern(rel).call()
            return JSONObject().put("ok", true).put("path", rel)
                .put("remaining", JSONArray(it.status().call().conflicting.toList().sorted()))
                .put("state", status(dir))
        }
    }

    // ---------- 内部 ----------

    private fun open(cwd: File): Git {
        val dir = checkedDir(cwd)
        val git = try {
            Git.open(dir)
        } catch (e: Exception) {
            fail(404, "当前目录不是 Git 仓库：${e.message}")
        }
        val work = git.repository.workTree?.canonicalFile
        if (work == null || (work != root && !work.path.startsWith(rootPath + File.separator))) {
            git.close()
            fail(403, "Git 仓库根不在工作区内")
        }
        return git
    }

    private fun openOrNull(dir: File): Git? = try { Git.open(dir) } catch (_: Exception) { null }

    private fun checkedDir(dir: File): File {
        val canonical = try { dir.canonicalFile } catch (e: Exception) { fail(400, "路径不合法：${e.message}") }
        if (canonical != root && !canonical.path.startsWith(rootPath + File.separator)) {
            fail(403, "路径越界")
        }
        if (!canonical.exists()) fail(404, "路径不存在")
        if (!canonical.isDirectory) fail(400, "cwd 不是目录")
        return canonical
    }

    private fun safeRepoPath(repo: Repository, raw: String): File {
        val rel = normalizeRel(raw)
        val work = repo.workTree.canonicalFile
        val f = File(work, rel).canonicalFile
        if (f != work && !f.path.startsWith(work.path + File.separator)) fail(403, "冲突路径越界")
        if (!f.path.startsWith(rootPath + File.separator) && f != root) fail(403, "路径越界")
        return f
    }

    private fun normalizeRel(raw: String): String {
        val rel = raw.replace('\\', '/').trim().removePrefix("./")
        if (rel.isEmpty() || rel.startsWith("/") || rel.contains("..")) fail(400, "路径不合法")
        return rel
    }

    private fun validateBranch(raw: String): String {
        val name = raw.trim()
        if (name.isEmpty() || name.startsWith("-") || name.contains("..") ||
            !name.matches(Regex("[A-Za-z0-9._/-]+"))) fail(400, "分支名不合法")
        return name
    }

    private fun trackingOf(repo: Repository, branch: String): String = try {
        BranchTrackingStatus.of(repo, branch)?.remoteTrackingBranch ?: ""
    } catch (_: Exception) { "" }

    private fun fileEntries(st: org.eclipse.jgit.api.Status): JSONArray {
        data class E(var index: String = " ", var worktree: String = " ",
                     var staged: Boolean = false, var untracked: Boolean = false, var unmerged: Boolean = false)
        val map = LinkedHashMap<String, E>()
        fun at(path: String): E = map.getOrPut(path) { E() }
        for (p in st.conflicting) { val e = at(p); e.index = "U"; e.worktree = "U"; e.unmerged = true }
        for (p in st.added) { val e = at(p); e.index = "A"; e.staged = true }
        for (p in st.changed) { val e = at(p); e.index = "M"; e.staged = true }
        for (p in st.removed) { val e = at(p); e.index = "D"; e.staged = true }
        for (p in st.modified) { at(p).worktree = "M" }
        for (p in st.missing) { at(p).worktree = "D" }
        for (p in st.untracked) { val e = at(p); e.worktree = "?"; e.untracked = true }
        val arr = JSONArray()
        for ((path, e) in map) arr.put(JSONObject().put("path", path).put("index", e.index)
            .put("worktree", e.worktree).put("staged", e.staged)
            .put("untracked", e.untracked).put("unmerged", e.unmerged))
        return arr
    }

    private fun stageId(repo: Repository, rel: String, stage: Int): ObjectId? {
        val dc = DirCache.read(repo)
        for (i in 0 until dc.entryCount) {
            val e = dc.getEntry(i)
            if (e.pathString == rel && e.stage == stage) return e.objectId
        }
        return null
    }

    private fun stageTexts(repo: Repository, rel: String): Map<String, Pair<Boolean, String>> {
        val out = LinkedHashMap<String, Pair<Boolean, String>>()
        for ((label, stage) in listOf("base" to 1, "ours" to 2, "theirs" to 3)) {
            val id = stageId(repo, rel, stage)
            if (id == null) { out[label] = false to ""; continue }
            val bytes = try { repo.newObjectReader().use { r -> r.open(id).bytes } } catch (_: Exception) { ByteArray(0) }
            val binary = RawTextBinary.isBinary(bytes)
            out[label] = binary to if (binary) "" else truncate(bytes)
        }
        return out
    }

    private fun readWorkText(path: File): Pair<Boolean, String> = try {
        val bytes = path.readBytes()
        val binary = RawTextBinary.isBinary(bytes)
        binary to if (binary) "" else truncate(bytes)
    } catch (_: Exception) { false to "" }

    private fun truncate(bytes: ByteArray): String =
        String(bytes, StandardCharsets.UTF_8).take(200_000)

    private fun createCheckpoint(repo: Repository): JSONObject {
        val head = repo.resolve(Constants.HEAD) ?: fail(409, "还没有提交，无法创建合并检查点")
        val ref = "refs/pocketmind/premerge/${System.currentTimeMillis()}"
        val ru = repo.updateRef(ref)
        ru.isForceUpdate = true
        ru.setNewObjectId(head)
        val res = ru.update()
        if (res == RefUpdate.Result.REJECTED || res == RefUpdate.Result.IO_FAILURE) {
            fail(409, "创建合并检查点失败：$res")
        }
        val cp = JSONObject().put("ref", ref).put("sha", head.name).put("short", head.name.take(8))
        File(repo.directory, "pocketmind-premerge").writeText(cp.toString(), Charsets.UTF_8)
        return cp
    }

    private fun readCheckpoint(repo: Repository): JSONObject? {
        val f = File(repo.directory, "pocketmind-premerge")
        if (!f.isFile) return null
        return try { JSONObject(f.readText()) } catch (_: Exception) { null }
    }

    private fun clearCheckpoint(repo: Repository) {
        val cp = readCheckpoint(repo)
        if (cp != null) {
            val ref = cp.optString("ref")
            if (ref.isNotEmpty()) {
                val ru = repo.updateRef(ref)
                ru.isForceUpdate = true
                ru.delete()
            }
        }
        File(repo.directory, "pocketmind-premerge").delete()
    }

    private fun clearMergeFiles(repo: Repository) {
        repo.writeMergeHeads(emptyList())
        for (name in listOf("MERGE_HEAD", "MERGE_MSG", "MERGE_MODE")) {
            File(repo.directory, name).delete()
        }
    }

    private fun RepositoryState.isMergingState(): Boolean =
        this == RepositoryState.MERGING || this == RepositoryState.MERGING_RESOLVED

    // ---------- 三路块（diff3） ----------

    private data class Change(val start: Int, val end: Int, val lines: List<String>)
    private data class Block(val index: Int, val base: String, val ours: String, val theirs: String) {
        fun valueOf(side: String): String = when (side) {
            "ours" -> ours
            "theirs" -> theirs
            "base" -> base
            else -> ""
        }
    }
    private data class Segment(val text: String, val block: Block?)
    private data class ThreeWay(val segments: List<Segment>, val blocks: List<Block>)

    private fun threeWay(baseText: String, oursText: String, theirsText: String): ThreeWay {
        val baseLines = splitLines(baseText)
        val oursLines = splitLines(oursText)
        val theirsLines = splitLines(theirsText)
        val oursEdits = diffs(baseLines, oursLines)
        val theirsEdits = diffs(baseLines, theirsLines)
        val segments = ArrayList<Segment>()
        val blocks = ArrayList<Block>()
        var pos = 0
        var oi = 0
        var ti = 0
        fun visible(edits: List<Change>, at: Int) = edits[at].start < pos || edits[at].start == pos
        while (oi < oursEdits.size || ti < theirsEdits.size) {
            val o = oursEdits.getOrNull(oi)
            val t = theirsEdits.getOrNull(ti)
            if (o == null || t == null) {
                val e = o ?: t!!
                if (e.start > pos) {
                    segments.add(Segment(joinLines(baseLines.subList(pos, e.start)), null))
                    pos = e.start
                }
                segments.add(Segment(joinLines(e.lines), null))
                pos = e.end
                if (o != null) oi++ else ti++
                continue
            }
            if (o.end <= t.start || t.end <= o.start) {
                // 两边修改不重叠：先处理更早的一处，另一处留到下一轮
                val e = if (o.start <= t.start) o else t
                if (e.start > pos) {
                    segments.add(Segment(joinLines(baseLines.subList(pos, e.start)), null))
                }
                segments.add(Segment(joinLines(e.lines), null))
                pos = e.end
                if (e === o) oi++ else ti++
                continue
            }
            // 冲突簇：把与 [start,end) 相交的所有修改并进来
            var start = minOf(o.start, t.start)
            var end = maxOf(o.end, t.end)
            var oEnd = oi
            var tEnd = ti
            var changed = true
            while (changed) {
                changed = false
                while (oEnd < oursEdits.size && touches(oursEdits[oEnd], start, end)) {
                    end = maxOf(end, oursEdits[oEnd].end); oEnd++; changed = true
                }
                while (tEnd < theirsEdits.size && touches(theirsEdits[tEnd], start, end)) {
                    end = maxOf(end, theirsEdits[tEnd].end); tEnd++; changed = true
                }
            }
            if (start > pos) segments.add(Segment(joinLines(baseLines.subList(pos, start)), null))
            val block = Block(
                index = blocks.size,
                base = joinLines(baseLines.subList(start, end)),
                ours = sideText(baseLines, oursEdits.subList(oi, oEnd), start, end),
                theirs = sideText(baseLines, theirsEdits.subList(ti, tEnd), start, end),
            )
            blocks.add(block)
            segments.add(Segment("", block))
            pos = end
            oi = oEnd
            ti = tEnd
        }
        if (pos < baseLines.size) segments.add(Segment(joinLines(baseLines.subList(pos, baseLines.size)), null))
        return ThreeWay(segments, blocks)
    }

    private fun touches(e: Change, start: Int, end: Int): Boolean {
        if (e.start == e.end) return e.start in start..end
        return e.start < end && e.end > start
    }

    private fun sideText(base: List<String>, edits: List<Change>, start: Int, end: Int): String {
        val sb = StringBuilder()
        var p = start
        for (e in edits) {
            if (e.start > p) sb.append(joinLines(base.subList(p, e.start)))
            sb.append(joinLines(e.lines))
            p = e.end
        }
        if (p < end) sb.append(joinLines(base.subList(p, end)))
        return sb.toString()
    }

    private fun diffs(base: List<String>, other: List<String>): List<Change> {
        val algorithm = HistogramDiff()
        val b = RawText(toBytes(base))
        val o = RawText(toBytes(other))
        return algorithm.diff(RawTextComparator.DEFAULT, b, o).map { e ->
            Change(e.beginA, e.endA, other.subList(e.beginB, e.endB))
        }
    }

    private fun splitLines(text: String): List<String> = text.split("\n")
    private fun joinLines(lines: List<String>): String = lines.joinToString("\n")
    private fun toBytes(lines: List<String>): ByteArray = joinLines(lines).toByteArray(Charsets.UTF_8)

    private fun fail(status: Int, message: String): Nothing =
        throw NativeWorkspace.WorkspaceException(status, message)
}

private object RawTextBinary {
    fun isBinary(bytes: ByteArray): Boolean {
        val n = minOf(bytes.size, 2048)
        for (i in 0 until n) if (bytes[i] == 0.toByte()) return true
        return false
    }
}
