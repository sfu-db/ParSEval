CREATE TABLE IF NOT EXISTS "badges" ("Id" INT, "UserId" INT, "Name" VARCHAR, "Date" TIMESTAMP, PRIMARY KEY ("Id"));

CREATE TABLE IF NOT EXISTS "comments" ("Id" INT, "PostId" INT, "Score" INT, "Text" VARCHAR, "CreationDate" TIMESTAMP, "UserId" INT, "UserDisplayName" VARCHAR, PRIMARY KEY ("Id"));

CREATE TABLE IF NOT EXISTS "postHistory" ("Id" INT, "PostHistoryTypeId" INT, "PostId" INT, "RevisionGUID" VARCHAR, "CreationDate" TIMESTAMP, "UserId" INT, "Text" VARCHAR, "Comment" VARCHAR, "UserDisplayName" VARCHAR, PRIMARY KEY ("Id"));

CREATE TABLE IF NOT EXISTS "postLinks" ("Id" INT, "CreationDate" TIMESTAMP, "PostId" INT, "RelatedPostId" INT, "LinkTypeId" INT, PRIMARY KEY ("Id"));

CREATE TABLE IF NOT EXISTS "posts" ("Id" INT, "PostTypeId" INT, "AcceptedAnswerId" INT, "CreaionDate" TIMESTAMP, "Score" INT, "ViewCount" INT, "Body" VARCHAR, "OwnerUserId" INT, "LasActivityDate" TIMESTAMP, "Title" VARCHAR, "Tags" VARCHAR, "AnswerCount" INT, "CommentCount" INT, "FavoriteCount" INT, "LastEditorUserId" INT, "LastEditDate" TIMESTAMP, "CommunityOwnedDate" TIMESTAMP, "ParentId" INT, "ClosedDate" TIMESTAMP, "OwnerDisplayName" VARCHAR, "LastEditorDisplayName" VARCHAR, PRIMARY KEY ("Id"));

CREATE TABLE IF NOT EXISTS "tags" ("Id" INT, "TagName" VARCHAR, "Count" INT, "ExcerptPostId" INT, "WikiPostId" INT, PRIMARY KEY ("Id"));

CREATE TABLE IF NOT EXISTS "users" ("Id" INT, "Reputation" INT, "CreationDate" TIMESTAMP, "DisplayName" VARCHAR, "LastAccessDate" TIMESTAMP, "WebsiteUrl" VARCHAR, "Location" VARCHAR, "AboutMe" VARCHAR, "Views" INT, "UpVotes" INT, "DownVotes" INT, "AccountId" INT, "Age" INT, "ProfileImageUrl" VARCHAR, PRIMARY KEY ("Id"));

CREATE TABLE IF NOT EXISTS "votes" ("Id" INT, "PostId" INT, "VoteTypeId" INT, "CreationDate" DATE, "UserId" INT, "BountyAmount" INT, PRIMARY KEY ("Id"));

SELECT T1.PostHistoryTypeId, (SELECT COUNT(DISTINCT UserId) FROM comments WHERE PostId = 3720) AS NumberOfUsers FROM postHistory AS T1 WHERE T1.PostId = 3720;

SELECT T1.PostHistoryTypeId, COUNT(DISTINCT T2.UserId) FROM postHistory AS T1 LEFT JOIN comments AS T2 ON T1.PostId = T2.PostId WHERE T1.PostId = 3720