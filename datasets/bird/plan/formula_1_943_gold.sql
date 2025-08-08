{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "AVG",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [
        {
          "column": 0,
          "type": "REAL"
        }
      ],
      "type": "REAL",
      "name": "EXPR$0"
    }
  ],
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "PLUS",
          "operator": "+",
          "type": "REAL",
          "operands": [
            {
              "kind": "TIMES",
              "operator": "*",
              "type": "INTEGER",
              "operands": [
                {
                  "kind": "CAST",
                  "operator": "CAST",
                  "type": "INTEGER",
                  "operands": [
                    {
                      "kind": "OTHER_FUNCTION",
                      "operator": "SUBSTR",
                      "type": "VARCHAR",
                      "operands": [
                        {
                          "kind": "INPUT_REF",
                          "index": 24,
                          "name": "$24",
                          "type": "VARCHAR"
                        },
                        {
                          "kind": "LITERAL",
                          "value": 1,
                          "type": "INTEGER",
                          "nullable": false,
                          "precision": 10
                        },
                        {
                          "kind": "MINUS",
                          "operator": "-",
                          "type": "INTEGER",
                          "operands": [
                            {
                              "kind": "OTHER_FUNCTION",
                              "operator": "INSTR",
                              "type": "INTEGER",
                              "operands": [
                                {
                                  "kind": "INPUT_REF",
                                  "index": 24,
                                  "name": "$24",
                                  "type": "VARCHAR"
                                },
                                {
                                  "kind": "LITERAL",
                                  "value": ":",
                                  "type": "CHAR",
                                  "nullable": false,
                                  "precision": 1
                                }
                              ]
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
                    }
                  ]
                },
                {
                  "kind": "LITERAL",
                  "value": 60,
                  "type": "INTEGER",
                  "nullable": false,
                  "precision": 10
                }
              ]
            },
            {
              "kind": "CAST",
              "operator": "CAST",
              "type": "REAL",
              "operands": [
                {
                  "kind": "OTHER_FUNCTION",
                  "operator": "SUBSTR",
                  "type": "VARCHAR",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 24,
                      "name": "$24",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "PLUS",
                      "operator": "+",
                      "type": "INTEGER",
                      "operands": [
                        {
                          "kind": "OTHER_FUNCTION",
                          "operator": "INSTR",
                          "type": "INTEGER",
                          "operands": [
                            {
                              "kind": "INPUT_REF",
                              "index": 24,
                              "name": "$24",
                              "type": "VARCHAR"
                            },
                            {
                              "kind": "LITERAL",
                              "value": ":",
                              "type": "CHAR",
                              "nullable": false,
                              "precision": 1
                            }
                          ]
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
                }
              ]
            }
          ]
        }
      ],
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
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
                    "index": 5,
                    "name": "$5",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "Hamilton",
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
                    "index": 4,
                    "name": "$4",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "Lewis",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
                  }
                ]
              }
            ]
          },
          "variableset": "[]",
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
                    "index": 0,
                    "name": "$0",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 11,
                    "name": "$11",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "drivers",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "results",
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