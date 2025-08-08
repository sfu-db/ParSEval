{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "DIVIDE",
      "operator": "/",
      "type": "REAL",
      "operands": [
        {
          "kind": "TIMES",
          "operator": "*",
          "type": "REAL",
          "operands": [
            {
              "kind": "CAST",
              "operator": "CAST",
              "type": "REAL",
              "operands": [
                {
                  "kind": "INPUT_REF",
                  "index": 0,
                  "name": "$0",
                  "type": "INTEGER"
                }
              ]
            },
            {
              "kind": "LITERAL",
              "value": 100,
              "type": "INTEGER",
              "nullable": false,
              "precision": 10
            }
          ]
        },
        {
          "kind": "CASE",
          "operator": "CASE",
          "type": "BIGINT",
          "operands": [
            {
              "kind": "EQUALS",
              "operator": "=",
              "type": "BOOLEAN",
              "operands": [
                {
                  "kind": "INPUT_REF",
                  "index": 1,
                  "name": "$1",
                  "type": "BIGINT"
                },
                {
                  "kind": "LITERAL",
                  "value": 0,
                  "type": "BIGINT",
                  "nullable": false,
                  "precision": 19
                }
              ]
            },
            {
              "kind": "LITERAL",
              "value": "NULL",
              "type": "BIGINT",
              "nullable": true,
              "precision": 19
            },
            {
              "kind": "CAST",
              "operator": "CAST",
              "type": "BIGINT",
              "operands": [
                {
                  "kind": "INPUT_REF",
                  "index": 1,
                  "name": "$1",
                  "type": "BIGINT"
                }
              ]
            }
          ]
        }
      ]
    }
  ],
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalAggregate",
      "keys": [],
      "aggs": [
        {
          "operator": "SUM",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 0,
              "type": "INTEGER"
            }
          ],
          "type": "INTEGER",
          "name": null
        },
        {
          "operator": "COUNT",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [],
          "type": "BIGINT",
          "name": null
        }
      ],
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "CASE",
              "operator": "CASE",
              "type": "INTEGER",
              "operands": [
                {
                  "kind": "AND",
                  "operator": "AND",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "EQUALS",
                      "operator": "=",
                      "type": "BOOLEAN",
                      "operands": [
                        {
                          "kind": "INPUT_REF",
                          "index": 22,
                          "name": "$22",
                          "type": "VARCHAR"
                        },
                        {
                          "kind": "LITERAL",
                          "value": "Chinese Simplified",
                          "type": "VARCHAR",
                          "nullable": false,
                          "precision": -1
                        }
                      ]
                    },
                    {
                      "kind": "EQUALS",
                      "operator": "=",
                      "type": "BOOLEAN",
                      "operands": [
                        {
                          "kind": "INPUT_REF",
                          "index": 8,
                          "name": "$8",
                          "type": "INTEGER"
                        },
                        {
                          "kind": "LITERAL",
                          "value": 1,
                          "type": "INTEGER",
                          "nullable": false,
                          "precision": 10
                        }
                      ]
                    }
                  ]
                },
                {
                  "kind": "LITERAL",
                  "value": 1,
                  "type": "INTEGER",
                  "nullable": false,
                  "precision": 10
                },
                {
                  "kind": "LITERAL",
                  "value": 0,
                  "type": "INTEGER",
                  "nullable": false,
                  "precision": 10
                }
              ]
            }
          ],
          "id": "3",
          "inputs": [
            {
              "relOp": "LogicalJoin",
              "joinType": "inner",
              "condition": {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 4,
                    "name": "$4",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 23,
                    "name": "$23",
                    "type": "VARCHAR"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "sets",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "set_translations",
                  "id": "1",
                  "inputs": []
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}